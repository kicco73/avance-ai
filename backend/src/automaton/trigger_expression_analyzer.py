"""Static analysis of a single trigger/`env:` expression string — every
identifier or namespace attribute it references, and whether any ordering
comparison mixes incompatible static types. Used both by AutomatonBuilder
(build-time validation) and by Automaton itself (trigger/signal evaluation)."""
from __future__ import annotations

import ast
import io
import tokenize
from typing import Iterable

from automaton.model import Signal


class TriggerExpressionAnalyzer:
    """Everything a trigger/`env:` expression's own text can be statically
    analyzed for, without evaluating it: which identifiers/namespaces it
    references, and whether an ordering comparison mixes incompatible types."""
    RESERVED_NAMESPACES = (
        "signal", "env", "session", "user", "source", "task", "chat", "attachment", "drive", "media", "metric",
        "datetime",
    )
    NESTED_NAMESPACES = (("session", "metric"), ("datetime", "timezone"))

    _NAMESPACE_PATHS: tuple[tuple[str, ...], ...] = tuple((ns,) for ns in RESERVED_NAMESPACES) + NESTED_NAMESPACES

    @staticmethod
    def _maximal_attribute_nodes(tree: ast.AST) -> list[ast.Attribute]:
        """Every ast.Attribute node in `tree` not nested inside a longer
        attribute chain, so a dotted chain is matched against its full,
        longest namespace path (see _namespace_path_of), never a shorter prefix."""
        nested_value_ids = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        return [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and id(node) not in nested_value_ids]

    @classmethod
    def _namespace_path_of(cls, node: ast.Attribute) -> tuple[tuple[str, ...], str] | None:
        """(namespace_path, leaf_attr) for `node` if its full dotted chain,
        root to leaf, is exactly one of _NAMESPACE_PATHS plus one more
        attribute (e.g. `signal.mood` -> (("signal",), "mood")) — None otherwise."""
        attrs = [node.attr]
        cur = node.value
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        chain = (cur.id, *reversed(attrs))
        path, leaf = chain[:-1], chain[-1]
        return (path, leaf) if path in cls._NAMESPACE_PATHS else None

    @classmethod
    def namespace_attrs(cls, tree: ast.AST, *namespace: str) -> set[str]:
        refs = (cls._namespace_path_of(node) for node in cls._maximal_attribute_nodes(tree))
        return {ref[1] for ref in refs if ref is not None and ref[0] == namespace}

    @classmethod
    def signal_names(cls, expression: str) -> set[str]:
        """Every `signal.<name>` referenced in a trigger/env expression, e.g.
        "signal.daysSinceLastEvent >= 85" -> {"daysSinceLastEvent"}."""
        tree = ast.parse(expression, mode="eval")
        return cls.namespace_attrs(tree, "signal")

    @classmethod
    def bare_names(cls, expression: str, namespaces: Iterable[str] = ()) -> set[str]:
        """Every identifier referenced *outside* one of the reserved
        namespaces (see RESERVED_NAMESPACES) or `namespaces` — in practice a core metric name.
        A nested-namespace root (see NESTED_NAMESPACES) is excluded too,
        and so is a name a comprehension binds itself (`for key, value in
        ...`) or a lambda takes as a parameter — each exists only inside
        the expression."""
        tree = ast.parse(expression, mode="eval")
        reserved = set(cls.RESERVED_NAMESPACES) | set(namespaces)
        namespace_bases = {
            node.value.id for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in reserved
        }
        comprehension_targets = {
            name.id for node in ast.walk(tree) if isinstance(node, ast.comprehension)
            for name in ast.walk(node.target) if isinstance(name, ast.Name)
        }
        lambda_parameters = {
            argument.arg for node in ast.walk(tree) if isinstance(node, ast.Lambda)
            for argument in node.args.args
        }
        return (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            - namespace_bases - comprehension_targets - lambda_parameters
        )

    @classmethod
    def _dynamic_namespace_refs(cls, expression: str, namespace: str) -> dict[str, set[str]]:
        """Every `<namespace>.<name>.<method>` reference in `expression`,
        grouped by name — shared by source_refs/media_refs below:
        `source.<name>`/`media.<doc_id>` are both dynamic, per-project
        namespaces static-tuple matching (_namespace_path_of/
        _NAMESPACE_PATHS) can't express, so each is matched directly here
        instead, by its own full three-part dotted chain."""
        tree = ast.parse(expression, mode="eval").body
        refs: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            chain = cls._dotted_chain(node)
            if chain is None or len(chain) != 3 or chain[0] != namespace:
                continue
            refs.setdefault(chain[1], set()).add(chain[2])
        return refs

    @classmethod
    def _dynamic_namespace_calls(
        cls, expression: str, namespace: str,
    ) -> list[tuple[str, str, int, tuple[str, ...], bool]]:
        """Every `<namespace>.<name>.<method>(...)` call in `expression`,
        as (name, method, positional_count, keyword_names, unpacks) —
        `unpacks` when a `*args`/`**kwargs` argument makes the count
        unknowable before evaluation. Shared by source_calls/media_calls."""
        tree = ast.parse(expression, mode="eval").body
        calls = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            chain = cls._dotted_chain(node.func)
            if chain is None or len(chain) != 3 or chain[0] != namespace:
                continue
            unpacks = any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords)
            keywords = tuple(kw.arg for kw in node.keywords if kw.arg is not None)
            calls.append((chain[1], chain[2], len(node.args), keywords, unpacks))
        return calls

    @classmethod
    def source_refs(cls, expression: str) -> dict[str, set[str]]:
        """Every `source.<name>.<method>` reference in `expression`,
        grouped by source name."""
        return cls._dynamic_namespace_refs(expression, "source")

    @classmethod
    def source_calls(cls, expression: str) -> list[tuple[str, str, int, tuple[str, ...], bool]]:
        """Every `source.<name>.<method>(...)` call in `expression`, as
        (source_name, method, positional_count, keyword_names, unpacks)."""
        return cls._dynamic_namespace_calls(expression, "source")

    @classmethod
    def media_refs(cls, expression: str) -> dict[str, set[str]]:
        """Every `media.<doc_id>.<method>` reference in `expression`,
        grouped by doc id — media.<doc_id> is a dynamic, per-project
        namespace the same way source.<name> is, one entry per file
        uploaded under this project's own `media/` folder (see
        automaton.file_types.media_doc_id_for)."""
        return cls._dynamic_namespace_refs(expression, "media")

    @classmethod
    def media_calls(cls, expression: str) -> list[tuple[str, str, int, tuple[str, ...], bool]]:
        """Every `media.<doc_id>.<method>(...)` call in `expression`, as
        (doc_id, method, positional_count, keyword_names, unpacks)."""
        return cls._dynamic_namespace_calls(expression, "media")

    @staticmethod
    def merged_string_arguments(expression: str) -> list[str]:
        """Every call argument written as two or more adjacent string
        literals (`'caso' '='`) — Python silently joins them into one
        argument, which is almost always a missing comma. Returned as the
        argument's own source text, for the error message."""
        tree = ast.parse(expression, mode="eval").body
        merged = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                segment = ast.get_source_segment(expression, arg) or ""
                strings = [
                    token for token in tokenize.generate_tokens(io.StringIO(segment).readline)
                    if token.type == tokenize.STRING
                ]
                if len(strings) > 1:
                    merged.append(segment)
        return merged

    @classmethod
    def namespace_refs(cls, expression: str) -> dict[str, set[str]]:
        """Every namespace attribute reference in `expression`, keyed by its
        dotted path (e.g. "session.metric"), one entry per namespace actually
        used — a namespace nothing references is absent, never an empty set."""
        tree = ast.parse(expression, mode="eval")
        refs: dict[str, set[str]] = {}
        for node in cls._maximal_attribute_nodes(tree):
            ref = cls._namespace_path_of(node)
            if ref is None:
                continue
            path, leaf = ref
            refs.setdefault(".".join(path), set()).add(leaf)
        return refs

    @classmethod
    def namespace_calls(cls, expression: str, *namespace: str) -> list[tuple[str, int, tuple[str, ...], bool]]:
        """Every `<namespace>.<method>(...)` call in `expression`, as
        (method, positional_count, keyword_names, unpacks) — the same
        shape _dynamic_namespace_calls returns for source/media, so
        AutomatonValidator checks all three against inspect's own bind
        rather than a count a `*args` method can't be described by."""
        tree = ast.parse(expression, mode="eval")
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            ref = cls._namespace_path_of(node.func)
            if ref is None or ref[0] != namespace:
                continue
            unpacks = any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords)
            keywords = tuple(kw.arg for kw in node.keywords if kw.arg is not None)
            calls.append((ref[1], len(node.args), keywords, unpacks))
        return calls

    @staticmethod
    def task_statements(source: str) -> list[tuple[int, str]]:
        """Splits `task` source into one (line_number, statement source)
        pair per top-level statement — one `task.<name>(...)` call each
        — using Python's own parser rather than naively splitting on '\\n'.
        This is what lets a single call span several lines (implicit
        continuation inside its own parens) and lets both a whole-line and
        a trailing '# ...' comment work exactly like they do in any other
        Python source, with no special-casing needed here at all — the
        tokenizer already strips comments and skips blank lines before the
        parser ever sees them. `line_number` is where that statement's own
        source starts, for error messages elsewhere. Each returned segment
        is handed to the exact same single-expression validators/evaluator
        every other caller here already uses (they parse it again
        themselves, in `mode="eval"` — multi-line is fine there too, only
        a real statement, e.g. an assignment, isn't). Raises SyntaxError,
        uncaught, for source that isn't valid Python at all — every caller
        already turns that into the same error a single malformed
        expression gets."""
        tree = ast.parse(source, mode="exec")
        return [(stmt.lineno, ast.get_source_segment(source, stmt)) for stmt in tree.body]

    @staticmethod
    def task_assignment(statement: str) -> tuple[str, str] | None:
        """(target_name, rhs_source) if `statement` (one already-split
        task_statements() segment) is a simple single-name assignment
        — `name = <expr>`, the only assignment shape a task line may
        take — None for anything else (a bare task/source call, or a
        shape (tuple/attribute/subscript target, chained `a = b = ...`)
        this deliberately doesn't support, left to fail the normal
        mode="eval" parse everywhere else the way any other malformed
        task line already does). Never raises on `statement` itself:
        it already parsed once, as part of task_statements()."""
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
            return None
        stmt = tree.body[0]
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return None
        return stmt.targets[0].id, ast.get_source_segment(statement, stmt.value)

    @staticmethod
    def on_exit_assignment(statement: str) -> tuple[str, str] | None:
        """(env_key, rhs_source) if `statement` (one already-split
        task_statements() segment) is an `env.<key> = <expr>`
        assignment — on-exit's env write (see
        AutomatonValidator.validate_on_exit/Automaton.eval_action_on_exit)
        — None for anything else (a chained `a = b = ...`, a
        tuple/subscript target, or an attribute target on anything other
        than bare `env`). A bare-name target is task_assignment's shape,
        which on-exit shares for its own locals: a value kept for the
        rest of the script and dropped once it ends. Never raises on
        `statement` itself: it already parsed once, as part of
        task_statements()."""
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
            return None
        stmt = tree.body[0]
        if len(stmt.targets) != 1:
            return None
        target = stmt.targets[0]
        if (
            not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name)
            or target.value.id != "env"
        ):
            return None
        return target.attr, ast.get_source_segment(statement, stmt.value)

    @staticmethod
    def bare_namespace_call(statement: str, namespace: str) -> str | None:
        """The method name if `statement` (one already-split
        task_statements() segment) is exactly a bare `<namespace>.<method>(...)`
        call — an ast.Expr wrapping a Call whose func is that two-level
        dotted attribute — None for anything else (an assignment, a bare
        literal, a call on a different namespace, or a call whose func
        isn't a plain two-level dotted name). Used by on-exit's own
        mixed grammar (AutomatonValidator.validate_on_exit) to recognize
        a `chat.<method>(...)` statement, the one shape (besides an
        `env.<key> = expr` assignment) an on-exit line may take. Never
        raises on `statement` itself: it already parsed once, as part of
        task_statements()."""
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
            return None
        call = tree.body[0].value
        if not isinstance(call, ast.Call):
            return None
        chain = TriggerExpressionAnalyzer._dotted_chain(call.func)
        if chain is None or len(chain) != 2 or chain[0] != namespace:
            return None
        return chain[1]
    _KIND_NUMBER = "number"
    _KIND_STRING = "string"
    _KIND_BOOL = "bool"
    _NUMERIC_KINDS = (_KIND_NUMBER, _KIND_BOOL)

    _FIXED_IDENTIFIER_KIND: dict[tuple[str, ...], dict[str, str]] = {
        ("session",): {
            "current_session_duration_in_minutes": _KIND_NUMBER,
            "last_user_session_datetime": _KIND_STRING,
            "number_of_user_sessions": _KIND_NUMBER,
            "state_duration_in_minutes": _KIND_NUMBER,
        },
        ("user",): {
            "provider": _KIND_STRING,
            "provider_user_id": _KIND_STRING,
            "email": _KIND_STRING,
            "name": _KIND_STRING,
            "picture_url": _KIND_STRING,
            "created_at": _KIND_STRING,
            "last_login": _KIND_STRING,
            "active_project": _KIND_STRING,
            "role": _KIND_STRING,
        },
    }
    _ALWAYS_NUMERIC_NAMESPACES = (("signal",), ("session", "metric"), ("metric",))

    _ORDERING_OPS: dict[type, str] = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
    _COMPARISON_OPS: dict[type, str] = {**_ORDERING_OPS, ast.Eq: "==", ast.NotEq: "!="}

    @classmethod
    def _leaf_kind(cls, node: ast.AST) -> str | None:
        """`node`'s statically-known kind ('number'/'string'/'bool'), or None
        (unknown, never "wrong") for a bare name, `env.*` reference, or
        sub-expression whose type isn't knowable ahead of a real turn."""
        if isinstance(node, ast.Call):
            return cls._leaf_kind(node.func)
        if isinstance(node, ast.Attribute):
            ref = cls._namespace_path_of(node)
            if ref is None:
                return None
            path, leaf = ref
            fixed = cls._FIXED_IDENTIFIER_KIND.get(path, {}).get(leaf)
            if fixed is not None:
                return fixed
            return cls._KIND_NUMBER if path in cls._ALWAYS_NUMERIC_NAMESPACES else None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return cls._KIND_BOOL
            if isinstance(node.value, (int, float)):
                return cls._KIND_NUMBER
            if isinstance(node.value, str):
                return cls._KIND_STRING
            return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = cls._leaf_kind(node.operand)
            return inner if inner in cls._NUMERIC_KINDS else None
        return None

    @classmethod
    def type_violations(cls, expression: str) -> list[str]:
        """Every ordering comparison (`<`/`<=`/`>`/`>=`, never `==`/`!=`) in
        `expression` between operands whose statically-known kinds (see
        _leaf_kind) are incompatible, e.g. `user.name >= 5`. Returns messages, never raises."""
        tree = ast.parse(expression, mode="eval").body
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for left, op, right in zip(operands, node.ops, operands[1:]):
                symbol = cls._ORDERING_OPS.get(type(op))
                if symbol is None:
                    continue
                left_kind, right_kind = cls._leaf_kind(left), cls._leaf_kind(right)
                if left_kind is None or right_kind is None:
                    continue
                if left_kind == right_kind or (left_kind in cls._NUMERIC_KINDS and right_kind in cls._NUMERIC_KINDS):
                    continue
                violations.append(
                    f"'{ast.unparse(left)} {symbol} {ast.unparse(right)}' compares a {left_kind} "
                    f"with a {right_kind} — this will raise a TypeError as soon as it's evaluated"
                )
        return violations

    _NOT_A_LITERAL = object()

    @classmethod
    def _signal_name_of(cls, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Attribute):
            return None
        ref = cls._namespace_path_of(node)
        return ref[1] if ref is not None and ref[0] == ("signal",) else None

    @classmethod
    def _literal_value(cls, node: ast.AST) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = cls._literal_value(node.operand)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return -inner
        return cls._NOT_A_LITERAL

    @classmethod
    def _outside_signal_domain(cls, value: object) -> bool:
        if value is cls._NOT_A_LITERAL:
            return False
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return True
        return not Signal.MIN_VALUE <= value <= Signal.MAX_VALUE

    @classmethod
    def signal_domain_violations(cls, expression: str) -> list[str]:
        """Every comparison in `expression` matching a `signal.*` against a
        literal no signal value can ever be — a string, a bool, or a number
        outside Signal.MIN_VALUE..MAX_VALUE. Returns messages, never raises."""
        tree = ast.parse(expression, mode="eval").body
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for left, op, right in zip(operands, node.ops, operands[1:]):
                symbol = cls._COMPARISON_OPS.get(type(op))
                if symbol is None:
                    continue
                for signal_side, literal_side in ((left, right), (right, left)):
                    name = cls._signal_name_of(signal_side)
                    if name is None or not cls._outside_signal_domain(cls._literal_value(literal_side)):
                        continue
                    violations.append(
                        f"'{ast.unparse(left)} {symbol} {ast.unparse(right)}' compares signal.{name} with "
                        f"{ast.unparse(literal_side)} — a signal is an integer between "
                        f"{Signal.MIN_VALUE} and {Signal.MAX_VALUE}"
                    )
        return violations

    _KIND_DATETIME = "datetime"
    _KIND_TIMEDELTA = "timedelta"

    @classmethod
    def _temporal_kind(cls, node: ast.AST) -> str | None:
        """`node`'s statically-known temporal kind: 'datetime' for
        `datetime.datetime(...)` / `datetime.datetime.now(...)` and for a
        datetime ± timedelta, 'timedelta' for `datetime.timedelta(...)`
        and a timedelta ± timedelta; None for anything else (including a
        bare `env.*`, whose runtime type nobody can know here)."""
        if isinstance(node, ast.Call):
            chain = cls._dotted_chain(node.func)
            if chain in (("datetime", "datetime"), ("datetime", "datetime", "now")):
                return cls._KIND_DATETIME
            if chain == ("datetime", "timedelta"):
                return cls._KIND_TIMEDELTA
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left, right = cls._temporal_kind(node.left), cls._temporal_kind(node.right)
            if left == cls._KIND_DATETIME and right == cls._KIND_TIMEDELTA:
                return cls._KIND_DATETIME
            if left == cls._KIND_TIMEDELTA and right == cls._KIND_TIMEDELTA:
                return cls._KIND_TIMEDELTA
            if isinstance(node.op, ast.Add) and left == cls._KIND_TIMEDELTA and right == cls._KIND_DATETIME:
                return cls._KIND_DATETIME
            return None
        return None

    @staticmethod
    def _dotted_chain(node: ast.AST) -> tuple[str, ...] | None:
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        return (node.id, *reversed(attrs))

    @classmethod
    def defer_violations(cls, expression: str) -> list[str]:
        """Everything that would make a `task.defer(act, when)` in
        `expression` unschedulable — or unhibernatable — at runtime,
        caught here instead: `act` must be a zero-argument `lambda:` (a
        bound method or any other value has no source to persist), and
        `when` must be of datetime kind by its *shape* (see _temporal_kind)
        — a bare `env.<key>` or string is refused even though it might
        hold a date at runtime. Inside a `datetime.timedelta(...)` the
        arguments may be anything numeric or unknown (`env.reminder_days`
        is fine); only a certain string is refused. Returns messages, never raises."""
        tree = ast.parse(expression, mode="eval")
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or cls._dotted_chain(node.func) != ("task", "defer"):
                continue
            if len(node.args) != 2 or node.keywords:
                continue
            act, when = node.args
            if not isinstance(act, ast.Lambda):
                violations.append(
                    f"task.defer(...): the first argument must be a `lambda: ...`, got '{ast.unparse(act)}'"
                )
            elif act.args.args or act.args.vararg or act.args.kwonlyargs or act.args.kwarg or act.args.posonlyargs:
                violations.append("task.defer(...): the lambda must take no arguments")
            if cls._temporal_kind(when) != cls._KIND_DATETIME:
                violations.append(
                    f"task.defer(...): `when` must be a datetime built from datetime.datetime(...) or "
                    f"datetime.datetime.now(...), optionally ± datetime.timedelta(...), got '{ast.unparse(when)}'"
                )
            for inner in ast.walk(when):
                if isinstance(inner, ast.Call) and cls._dotted_chain(inner.func) == ("datetime", "timedelta"):
                    for argument in (*inner.args, *(keyword.value for keyword in inner.keywords)):
                        if cls._leaf_kind(argument) == cls._KIND_STRING:
                            violations.append(
                                f"task.defer(...): datetime.timedelta() takes numbers, got the string "
                                f"'{ast.unparse(argument)}'"
                            )
        return violations

    @classmethod
    def attachment_read_violations(cls, expression: str) -> list[str]:
        """Every `attachment.read(...)` call in `expression` whose single
        argument isn't a string literal — the only shape
        AutomatonBuilder._validate_attachment_read can resolve against
        this project's own archives at build time (name must be known
        without evaluating anything). Returns messages, never raises."""
        tree = ast.parse(expression, mode="eval")
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or cls._dotted_chain(node.func) != ("attachment", "read"):
                continue
            if len(node.args) != 1 or node.keywords or not (
                isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                violations.append(
                    f"attachment.read(...): takes exactly one string literal argument, "
                    f"got '{ast.unparse(node)}'"
                )
        return violations

    @classmethod
    def attachment_read_names(cls, expression: str) -> set[str]:
        """Every literal `name` passed to a well-shaped `attachment.read(name)`
        call in `expression` — only call after attachment_read_violations(expression)
        is empty, since a malformed call is silently skipped here rather than raised."""
        tree = ast.parse(expression, mode="eval")
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or cls._dotted_chain(node.func) != ("attachment", "read"):
                continue
            if len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
        return names

    @classmethod
    def expression_kind(cls, expression: str) -> str | None:
        """`expression`'s own statically-known kind ('number'/'string'/
        'bool'), the same notion _leaf_kind uses for one comparison
        operand — exposed here for a caller checking type *consistency*
        (e.g. an env key's declared value vs. what an action's own
        `env:` writes to it) rather than an ordering comparison. None
        when the expression's kind isn't knowable ahead of a real turn
        (it reads env.*/a bare name, combines values via an operator
        this can't see through, or — for a caller passing already-
        untrusted text — isn't even valid syntax), never something to
        treat as a mismatch on its own."""
        try:
            tree = ast.parse(expression, mode="eval").body
        except SyntaxError:
            return None
        return cls._leaf_kind(tree)
