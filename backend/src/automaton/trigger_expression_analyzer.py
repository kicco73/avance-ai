"""Static analysis of a single trigger/`env:` expression string — every
identifier or namespace attribute it references, and whether any ordering
comparison mixes incompatible static types. Used both by AutomatonBuilder
(build-time validation) and by Automaton itself (trigger/signal evaluation)."""
from __future__ import annotations

import ast


class TriggerExpressionAnalyzer:
    """Everything a trigger/`env:` expression's own text can be statically
    analyzed for, without evaluating it: which identifiers/namespaces it
    references, and whether an ordering comparison mixes incompatible types."""

    # Reserved namespaces a trigger/env expression resolves against. `automaton`
    # has no entry in _NAMESPACE_PATHS below since automaton.<project>.state/
    # env.<key> is a dynamic, per-project chain static-tuple matching can't
    # express — same reason `source.<name>.<method>` is never matched through
    # here either (see source_refs, matched directly instead).
    RESERVED_NAMESPACES = (
        "signal", "env", "session", "user", "source", "actuator", "metric", "automaton", "datetime",
    )

    # Dotted sub-namespaces nested one level under a reserved namespace above —
    # each entry matches as a *whole* path, so `session.metric.<attr>` and plain
    # `session.<attr>` resolve to different namespaces.
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
    def bare_names(cls, expression: str) -> set[str]:
        """Every identifier referenced *outside* one of the reserved
        namespaces (see RESERVED_NAMESPACES) — in practice a core metric name.
        A nested-namespace root (see NESTED_NAMESPACES) is excluded too."""
        tree = ast.parse(expression, mode="eval")
        namespace_bases = {
            node.value.id for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in cls.RESERVED_NAMESPACES
        }
        return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} - namespace_bases

    @staticmethod
    def automaton_project_refs(expression: str) -> set[str]:
        """Every project name referenced as `automaton.<project>...` in
        `expression`. Walks every Attribute node (not just maximal ones),
        since a reference is meaningful at any depth in the chain."""
        tree = ast.parse(expression, mode="eval").body
        return {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "automaton"
        }

    @staticmethod
    def source_refs(expression: str) -> dict[str, set[str]]:
        """Every `source.<name>.<method>` reference in `expression`,
        grouped by source name — `source.<name>` is a dynamic, per-project
        namespace (like `automaton.<project>`, see automaton_project_refs)
        static-tuple matching (_namespace_path_of/_NAMESPACE_PATHS) can't
        express, so it's matched directly here instead."""
        tree = ast.parse(expression, mode="eval").body
        refs: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)):
                continue
            name_node = node.value
            if not isinstance(name_node.value, ast.Name) or name_node.value.id != "source":
                continue
            refs.setdefault(name_node.attr, set()).add(node.attr)
        return refs

    @staticmethod
    def automaton_env_refs(expression: str) -> dict[str, set[str]]:
        """Every `automaton.<project>.env.<key>` reference in `expression`,
        grouped by project. Only matches the specific 4-level chain, unlike
        the broader automaton_project_refs."""
        tree = ast.parse(expression, mode="eval").body
        refs: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)):
                continue
            env_node = node.value
            if env_node.attr != "env" or not isinstance(env_node.value, ast.Attribute):
                continue
            project_node = env_node.value
            if not isinstance(project_node.value, ast.Name) or project_node.value.id != "automaton":
                continue
            refs.setdefault(project_node.attr, set()).add(node.attr)
        return refs

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
    def namespace_calls(cls, expression: str, *namespace: str) -> list[tuple[str, int]]:
        tree = ast.parse(expression, mode="eval")
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            ref = cls._namespace_path_of(node.func)
            if ref is None or ref[0] != namespace:
                continue
            calls.append((ref[1], len(node.args) + len(node.keywords)))
        return calls

    @staticmethod
    def on_enter_statements(source: str) -> list[tuple[int, str]]:
        """Splits `on-enter` source into one (line_number, statement source)
        pair per top-level statement — one `actuator.<name>(...)` call each
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
    def on_enter_assignment(statement: str) -> tuple[str, str] | None:
        """(target_name, rhs_source) if `statement` (one already-split
        on_enter_statements() segment) is a simple single-name assignment
        — `name = <expr>`, the only assignment shape an on-enter line may
        take — None for anything else (a bare actuator/source call, or a
        shape (tuple/attribute/subscript target, chained `a = b = ...`)
        this deliberately doesn't support, left to fail the normal
        mode="eval" parse everywhere else the way any other malformed
        on-enter line already does). Never raises on `statement` itself:
        it already parsed once, as part of on_enter_statements()."""
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
            return None
        stmt = tree.body[0]
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return None
        return stmt.targets[0].id, ast.get_source_segment(statement, stmt.value)

    # Every identifier whose runtime *type* is fixed by its own contract, well
    # enough to check statically. `env.*` is absent: it's a free-form store any
    # expression can set to anything, so its type is treated as unknown.
    _KIND_NUMBER = "number"
    _KIND_STRING = "string"
    _KIND_BOOL = "bool"
    # A kind counts as "number-like" for ordering purposes: Python itself
    # treats bool as an int subtype (`True >= 0.5` is legal), so mixing the
    # two is never actually a runtime error.
    _NUMERIC_KINDS = (_KIND_NUMBER, _KIND_BOOL)

    _FIXED_IDENTIFIER_KIND: dict[tuple[str, ...], dict[str, str]] = {
        ("session",): {
            "current_session_duration_in_minutes": _KIND_NUMBER,
            "last_user_session_datetime": _KIND_STRING,
            "number_of_user_sessions": _KIND_NUMBER,
            "state_duration_in_minutes": _KIND_NUMBER,
        },
        # Every User field (db/models.py) is a plain string once
        # resolved (see db.users.UserMixin.get_user_facts's own
        # _utc_iso formatting for created_at/last_login) — none of
        # user.* is ever a number.
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
        # `source.<name>.*` is absent here for the same reason
        # `automaton.<project>.*` is: it's a dynamic, per-project chain
        # this static path -> kind lookup can't express (see source_refs
        # above) — its own kind is always unknown to this analyzer.
    }
    # Every identifier under these namespaces is always a number, no per-name
    # exceptions to look up — signals by contract, metrics because
    # BaseMetric.result always clamps into [0, 100] as a float.
    _ALWAYS_NUMERIC_NAMESPACES = (("signal",), ("session", "metric"), ("metric",))

    _ORDERING_OPS: dict[type, str] = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}

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

    # --- actuator.defer(lambda: ..., when) --------------------------------

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
        """Everything that would make an `actuator.defer(act, when)` in
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
            if not isinstance(node, ast.Call) or cls._dotted_chain(node.func) != ("actuator", "defer"):
                continue
            if len(node.args) != 2 or node.keywords:
                continue  # arity is reported by the builder's own check
            act, when = node.args
            if not isinstance(act, ast.Lambda):
                violations.append(
                    f"actuator.defer(...): the first argument must be a `lambda: ...`, got '{ast.unparse(act)}'"
                )
            elif act.args.args or act.args.vararg or act.args.kwonlyargs or act.args.kwarg or act.args.posonlyargs:
                violations.append("actuator.defer(...): the lambda must take no arguments")
            if cls._temporal_kind(when) != cls._KIND_DATETIME:
                violations.append(
                    f"actuator.defer(...): `when` must be a datetime built from datetime.datetime(...) or "
                    f"datetime.datetime.now(...), optionally ± datetime.timedelta(...), got '{ast.unparse(when)}'"
                )
            for inner in ast.walk(when):
                if isinstance(inner, ast.Call) and cls._dotted_chain(inner.func) == ("datetime", "timedelta"):
                    for argument in (*inner.args, *(keyword.value for keyword in inner.keywords)):
                        if cls._leaf_kind(argument) == cls._KIND_STRING:
                            violations.append(
                                f"actuator.defer(...): datetime.timedelta() takes numbers, got the string "
                                f"'{ast.unparse(argument)}'"
                            )
        return violations

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
