"""Compiles one project (its index.yml plus its own archives) into a
standalone Python package: the same automaton, built by literal Python
constructor calls instead of by parsing YAML at boot.

What the generated package contains:

    <name>/__init__.py   the minimal wiring to export AUTOMATON and
                         STORAGE_REVISION from <name>/<name>.py — nothing
                         else, so importing the package itself reveals
                         no more than that it has an automaton
    <name>/<name>.py     every State/Action/Signal/Reaction/EnvKey/Source
                         as a literal, and AUTOMATON, an instance of a
                         CompiledAutomaton composed from automaton.core's
                         CoreAutomaton plus the platform mixins this
                         product enables
    <name>/prompt.py     every AI-facing text as a named constant —
                         general_prompt, each state's contextual_prompt,
                         each signal's and reaction's definition, each
                         env key's and source's ai_definition
    <name>/data/         the project's own archive files, verbatim, read
                         off disk when a turn actually needs one (see
                         tracking.project_files.PackageProjectFiles)

Prompts sit apart because they are the part of a compiled product a
human still reads, tunes and translates, and the part that changes
without changing behaviour: editing one is then a one-file diff instead
of a diff inside generated control flow. prompt.py is generated like
everything else — recompiling overwrites it, and nothing stops anyone
from editing it in the meantime.

Behaviour is compiled too: every trigger, `env:` expression, on-exit
line and task statement is emitted as a real Python function, and the
generated automaton overrides the three primitives CoreAutomaton isolates
for the purpose. Nothing in the package evaluates a string. What it does
NOT override is everything around those primitives — the loops, the
ordering, the try/except, the warnings — which stays the platform's own
code, so a compiled automaton cannot drift from the interpreted one.

The emission is driven by dataclasses.fields(), never by a hand-written
list of field names: a field added to State or Action tomorrow is
emitted automatically instead of being silently dropped.

Compiler wraps the whole "one automaton in, (source of __init__.py,
source of prompt.py) out" pass — automaton/storage_revision/the running
PromptTable live on self instead of being threaded through every helper
call. compile_package/compile_contents/verify_package stay free
functions: they are the driver layer (I/O, the CLI, comparing two
already-built automatons), not part of compiling one.

Reachable two ways, both landing here: the Build view's own Target step
(see BuildController.post_build_project, which always builds a local
module into this package) and bin/compile_automaton.py, the CLI kept for
compiling a project directory or zip by hand.

--verify re-imports the package it just wrote and compares it, field by
field, against the automaton built from the same project by the ordinary
AutomatonBuilder: every state, every action, every signal, source, env
key and reaction, plus the derived answers (declared env key names, the
triggerable signal names of each state).
"""
from __future__ import annotations

import ast
import dataclasses
import keyword
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from automaton.automaton_builder import AutomatonBuilder
from automaton.identifier_registry import IdentifierRegistry
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.model import Action, EnvKey, Reaction, Signal, Source, State
from project.archive.layout import BUNDLE_FILE_NAMES
from project.archive.zip_importer import ZipImporter

_EXCLUDED_FILENAMES = frozenset(BUNDLE_FILE_NAMES)


class CompileError(Exception):
    """A project this compiler cannot turn into a package — always raised
    with enough context to find the cause in index.yml."""


# --- input, through the platform's own zip-import safety --------------------

def _is_zip(path: Path) -> bool:
    return path.is_file() and (path.suffix.lower() == ".zip" or zipfile.is_zipfile(path))


def read_project_contents(project_path: Path) -> dict[str, str | bytes]:
    if _is_zip(project_path):
        with tempfile.TemporaryDirectory(prefix="compile_automaton_") as staging:
            staging_dir = Path(staging)
            ZipImporter.extract_safely(project_path.read_bytes(), staging_dir)
            return _read_directory_contents(staging_dir)
    return _read_directory_contents(project_path)


def _read_directory_contents(project_dir: Path) -> dict[str, str | bytes]:
    contents: dict[str, str | bytes] = {}
    for path in sorted(project_dir.rglob("*")):
        if path.is_dir() or path.name in _EXCLUDED_FILENAMES:
            continue
        rel = path.relative_to(project_dir).as_posix()
        try:
            contents[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contents[rel] = path.read_bytes()
    return contents


def _constant_name(prefix: str, key: str, field_name: str) -> str:
    """A legal, readable, unique-per-(prefix, key, field) constant name.
    Project keys are not Python identifiers (`action-0` and hyphenated
    state keys are the norm, not the exception), so anything illegal
    becomes an underscore; collisions are caught by the caller, which
    keeps every generated name in one namespace."""
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in key).upper()
    suffix = f"_{field_name.upper()}" if field_name not in ("contextual_prompt", "definition", "ai_definition") else ""
    return f"{prefix}_{safe}{suffix}"


class PromptTable:
    """Collects every model-facing text under a unique constant name, so
    __init__.py can reference `prompt.<NAME>` wherever the value goes."""

    def __init__(self) -> None:
        self._by_name: dict[str, str] = {}
        self._names: dict[tuple[str, str, str], str] = {}

    def add_named(self, name: str, value: str) -> str:
        """For the one text that belongs to the project itself rather than
        to a state, signal, source or env key."""
        self._by_name[name] = value
        return name

    def add(self, prefix: str, key: str, field_name: str, value: str) -> str:
        name = _constant_name(prefix, key, field_name)
        existing = self._by_name.get(name)
        if existing is not None and existing != value:
            raise CompileError(
                f"two different texts both want the constant {name!r} in prompt.py "
                f"(from {prefix.lower()} {key!r}) — rename one of them in the project."
            )
        self._by_name[name] = value
        self._names[(prefix, key, field_name)] = name
        return name

    def render(self) -> str:
        return "\n".join(f"{name} = {value!r}\n" for name, value in sorted(self._by_name.items()))


class Compiler(object):
    """One automaton, compiled: Compiler(automaton, storage_revision).
    compile() -> (source of __init__.py, source of prompt.py)."""

    # Which dataclass field on which type holds text written for the
    # model. Everything named here is emitted as a constant in prompt.py
    # and referenced from __init__.py; everything else is emitted
    # inline. fixed_message is deliberately NOT here: it is a canned
    # reply handed to the user verbatim, not something the model reads.
    _PROMPT_FIELDS: dict[type, tuple[str, ...]] = {
        State: ("contextual_prompt",),
        Signal: ("definition",),
        Reaction: ("definition",),
        EnvKey: ("ai_definition",),
        Source: ("ai_definition",),
    }

    # The only scope entries that are plain dicts (see tracking/
    # evaluation_scope.py). An expression reads them as attributes
    # thanks to simpleeval's attribute-to-item fallback, so a compiled
    # module needs a real object with those attributes for
    # `signal.progress` to stay `signal.progress`. Everything else in
    # the scope is already an object and is bound straight through.
    _ADAPTERS = {"signal": "_Signals", "env": "_Env", "user": "_User"}

    _PREAMBLE = '''from __future__ import annotations

from pathlib import Path

from automaton.automaton import CompiledAutomaton, PayloadsMixin, IntrospectionMixin
from system.logging_factory import LoggerFactory
from automaton.model import Action, EnvKey, Reaction, Signal, Source, State

from . import prompt

_logger = LoggerFactory.get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
'''

    _AUTOMATON_CLASS = '''
class {class_name}(CompiledAutomaton, PayloadsMixin, IntrospectionMixin):
{seam_overrides}

AUTOMATON = {class_name}(
    init_action=_INIT_ACTION,
    states=_STATES,
    general_prompt=prompt.GENERAL_PROMPT,
    signals=_SIGNALS,
    general_attachments=_GENERAL_ATTACHMENTS,
    autotracking_on_ai_message={autotracking!r},
    env_keys=_ENV_KEYS,
    reactions=_REACTIONS,
    sources=_SOURCES,
    project_id={project_id!r},
    project_family={family!r},
    project_revision={project_revision!r},
    project_ui_label={ui_label!r},
    project_ui_description={ui_description!r},
    talk_enabled={talk_enabled!r},
    new_session_strategy={new_session_strategy!r},
    build_warnings={build_warnings!r},
)

AUTOMATON.archives_dir = _DATA_DIR
'''

    _SEAM_LOOKUP = '''
def _compiled(table, text, kind):
    compiled = table.get(text)
    if compiled is None:
        _logger.error("no compiled %s: %r", kind, text)
        raise KeyError(text)
    return compiled
'''

    _SEAM_OVERRIDES = '''
    @classmethod
    def _evaluate_expression(cls, expression, scope):
        if expression in _LITERALS:
            return _LITERALS[expression]
        return _compiled(_EXPRESSIONS, expression, "expression")(scope)

    @classmethod
    def _evaluate_statement(cls, statement, scope):
        return _compiled(_STATEMENTS, statement, "statement")(scope)

    @classmethod
    def _referenced_signal_names(cls, expression):
        return _SIGNAL_REFS[expression]
'''

    _NOT_LITERAL = object()

    def __init__(self, automaton: Any, storage_revision: int | None = None) -> None:
        self.automaton = automaton
        self.storage_revision = storage_revision
        self.prompts = PromptTable()

    def compile(self) -> tuple[str, str]:
        self._refuse_index_yml_as_attachment()
        self.prompts.add_named("GENERAL_PROMPT", self.automaton.general_prompt or "")

        parts: list[str] = [self._PREAMBLE]
        # Which stored revision this package was compiled from — what the
        # loader checks before serving it (see project.archive.
        # compiled_automaton_loader). The directory a package sits in names
        # the same number, but that is a convenience: this is the claim the
        # package itself makes, and the only one trusted. None for a build
        # from a directory or zip on disk, which has no stored revision.
        parts.append(f"STORAGE_REVISION = {self.storage_revision!r}")

        state_names: dict[str, str] = {}
        state_blocks: list[str] = []
        taken_state_names: set[str] = set()
        for key, state in self.automaton.states.items():
            name = self._unique_identifier("_STATE", key, taken_state_names)
            state_names[key] = name
            state_blocks.append(f"{name} = {self._state_literal(state)}")
        parts.append("\n\n".join(state_blocks))
        parts.append(
            "_STATES = {\n"
            + "".join(f"    {key!r}: {state_names[key]},\n" for key in self.automaton.states)
            + "}"
        )

        parts.append(f"_INIT_ACTION = {self._dataclass_literal(self.automaton.init_action, key='init')}")
        for attribute, variable in (
            ("signals", "_SIGNALS"), ("reactions", "_REACTIONS"),
            ("env_keys", "_ENV_KEYS"), ("sources", "_SOURCES"),
        ):
            items = getattr(self.automaton, attribute)
            rendered = "".join(f"    {self._dataclass_literal(i, key=i.name)},\n" for i in items)
            parts.append(f"{variable} = [\n{rendered}]" if items else f"{variable} = []")

        # Paths under data/, already resolved by the builder — the same
        # strings a State or Action carries, and the same ones the interpreted
        # automaton holds.
        parts.append(f"_GENERAL_ATTACHMENTS = {self._literal(self.automaton.general_attachments)}")
        parts.append(self._compile_seam())
        parts.append(self._AUTOMATON_CLASS.format(
            class_name=self._automaton_class_name(),
            seam_overrides=self._SEAM_OVERRIDES,
            autotracking=self.automaton.autotracking_on_ai_message,
            project_id=self.automaton.project_id,
            family=self.automaton.family,
            project_revision=self.automaton.project_revision,
            ui_label=self.automaton.project_ui_label,
            ui_description=self.automaton.project_ui_description,
            talk_enabled=self.automaton.talk_enabled,
            new_session_strategy=self.automaton.new_session_strategy,
            build_warnings=self.automaton.build_warnings,
        ))
        return "\n\n".join(parts) + "\n", self.prompts.render()

    # --- packaging guard -----------------------------------------------

    def _refuse_index_yml_as_attachment(self) -> None:
        """index.yml is compiled away in full and never copied into data/, so
        a project that hands it to the model as an attachment would silently
        lose it. Nothing does today; if something ever does, it must be a
        build error rather than a missing file at run time."""
        declaring = [
            where for where, names in
            [("the project itself", self.automaton.general_attachments)]
            + [(f"signal {s.name!r}", s.attachments) for s in self.automaton.signals]
            + [(f"state {key!r}", state.attachments) for key, state in self.automaton.states.items()]
            if "index.yml" in names
        ]
        if declaring:
            raise CompileError(
                "index.yml is declared as an attachment by " + ", ".join(declaring)
                + " — a compiled package does not ship it, since it is compiled away in full."
            )

    # --- literal emission, driven by dataclasses.fields() ---------------

    def _literal(self, value: Any) -> str:
        """A repr Python can read back. Only the shapes the model actually
        holds — anything else is a field this compiler has never seen and
        must be looked at rather than guessed at."""
        if value is None or isinstance(value, (str, bool, int, float)):
            return repr(value)
        if isinstance(value, tuple):
            return "(" + "".join(f"{self._literal(v)}, " for v in value) + ")"
        if isinstance(value, list):
            return "[" + ", ".join(self._literal(v) for v in value) + "]"
        if isinstance(value, dict):
            return "{" + ", ".join(f"{self._literal(k)}: {self._literal(v)}" for k, v in value.items()) + "}"
        if dataclasses.is_dataclass(value):
            return self._dataclass_literal(value)
        raise CompileError(
            f"cannot emit a literal for {type(value).__name__} ({value!r}) — a new field shape this compiler "
            "has not been taught about."
        )

    def _dataclass_literal(self, obj: Any, key: str | None = None) -> str:
        """`Type(field=<literal>, ...)` for every field the dataclass
        declares — read from dataclasses.fields(), so a field added upstream
        is emitted rather than quietly lost. Fields listed in _PROMPT_FIELDS
        are emitted as a reference into prompt.py instead of inline."""
        prompt_fields = self._PROMPT_FIELDS.get(type(obj), ())
        parts = []
        for field in dataclasses.fields(obj):
            value = getattr(obj, field.name)
            if field.name in prompt_fields and isinstance(value, str) and value:
                prefix = type(obj).__name__.upper()
                parts.append(f"{field.name}=prompt.{self.prompts.add(prefix, key or '', field.name, value)}")
            else:
                parts.append(f"{field.name}={self._literal(value)}")
        return f"{type(obj).__name__}({', '.join(parts)})"

    def _state_literal(self, state: State) -> str:
        """Like _dataclass_literal, but each action is emitted on its own
        line: a state with a dozen actions is otherwise one unreadable line."""
        parts = []
        for field in dataclasses.fields(state):
            value = getattr(state, field.name)
            if field.name == "actions":
                actions = "".join(f"\n        {self._dataclass_literal(a, key=f'{state.key}.{a.name}')}," for a in value)
                parts.append(f"    actions=[{actions}\n    ]")
            elif field.name in self._PROMPT_FIELDS[State] and isinstance(value, str) and value:
                parts.append(f"    {field.name}=prompt.{self.prompts.add('STATE', state.key, field.name, value)}")
            else:
                parts.append(f"    {field.name}={self._literal(value)}")
        return "State(\n" + ",\n".join(parts) + ",\n)"

    # --- naming -----------------------------------------------------------

    @classmethod
    def _identifier(cls, text: str) -> str:
        """`text` sanitized into a legal identifier fragment — non-identifier
        characters become underscores, a leading digit (or nothing at all)
        gets a leading underscore."""
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in text)
        return f"_{safe}" if not safe or safe[0].isdigit() else safe

    @classmethod
    def _unique_identifier(cls, prefix: str, text: str, taken: set[str]) -> str:
        """`prefix` + `text`'s own mangled identifier, deduplicated against
        every name `taken` already holds — two states/signals/actions whose
        keys mangle to the same identifier still get distinct Python names."""
        name = f"{prefix}_{cls._identifier(text)}"
        suffix = 2
        unique = name
        while unique in taken:
            unique = f"{name}_{suffix}"
            suffix += 1
        taken.add(unique)
        return unique

    def _automaton_class_name(self) -> str:
        """A PascalCase class name mangled from the project id (e.g.
        'hello_world' -> 'HelloWorldAutomaton') — relies only on project.id's
        own build-time guarantee of being a plain Python identifier, splits
        on '_' to make each word capitalized."""
        return "".join(
            part[:1].upper() + part[1:] for part in self.automaton.project_id.split("_") if part
        ) + "Automaton"

    # --- the seam, compiled -------------------------------------------
    # Turns the expression text an Action carries into real Python, so a
    # compiled automaton never evaluates a string. It replaces exactly
    # the three primitives CoreAutomaton isolates for the purpose —
    # _evaluate_expression, _evaluate_statement, _referenced_signal_names
    # (_SEAM_OVERRIDES above) — and nothing else: every loop, every
    # try/except, every warning around them stays the platform's own
    # code, so an interpreted and a compiled automaton cannot drift
    # apart on ordering, on what a failure means, or on what is
    # collected and what is skipped.

    def _collect_sources(self) -> tuple[list[str], list[str]]:
        """(expressions, statements) — every distinct piece of text this
        automaton would otherwise hand to an evaluator, split by which
        primitive receives it. In encounter order, so a regenerated module
        diffs cleanly against the one before it."""
        expressions: dict[str, None] = {}
        statements: dict[str, None] = {}
        actions = [self.automaton.init_action] + [
            a for state in self.automaton.states.values() for a in state.actions
        ]
        for action in actions:
            if action.trigger:
                expressions[action.trigger] = None
            for expression in (action.env or {}).values():
                expressions[expression] = None
            # on-exit: an `env.<key> = expr` line's right-hand side goes to
            # _evaluate_expression, a bare `chat.<method>(...)` line to
            # _evaluate_statement (see CoreAutomaton.eval_action_on_exit).
            for _line, statement in TriggerExpressionAnalyzer.task_statements(action.on_exit or ""):
                assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
                if assignment is not None:
                    expressions[assignment[1]] = None
                else:
                    statements[statement] = None
            # task: both halves go to _evaluate_statement, an assignment as its
            # right-hand side alone (see CoreAutomaton.render_task_script).
            for _line, statement in TriggerExpressionAnalyzer.task_statements(action.task or ""):
                assignment = TriggerExpressionAnalyzer.task_assignment(statement)
                statements[assignment[1] if assignment is not None else statement] = None
        return list(expressions), list(statements)

    def _compile_seam(self) -> str:
        """The whole compiled-behaviour section of __init__.py."""
        expressions, statements = self._collect_sources()
        all_sources = expressions + statements
        referenced = {root: self._referenced_attributes(all_sources, root) for root in self._ADAPTERS}
        blocks = [
            self._namespace_class(
                "_Signals", [s.name for s in self.automaton.signals if s.name in referenced["signal"]],
            ),
            self._namespace_class(
                "_Env", [e.name for e in self.automaton.env_keys if e.name in referenced["env"]],
            ),
            self._namespace_class("_User", sorted(referenced["user"] & set(IdentifierRegistry.USER))),
        ]

        functions: list[str] = []
        expression_table: list[str] = []
        literal_table: list[str] = []
        statement_table: list[str] = []
        signal_refs: list[str] = []
        for index, source in enumerate(expressions):
            literal = self._literal_expression_value(source)
            if literal is not self._NOT_LITERAL:
                literal_table.append(f"    {source!r}: {literal!r},")
            else:
                function_name = f"_expr_{index}"
                functions.append(self._compiled_function(function_name, source))
                expression_table.append(f"    {source!r}: {function_name},")
            signal_refs.append(
                f"    {source!r}: frozenset({sorted(TriggerExpressionAnalyzer.signal_names(source))!r}),"
            )
        for index, source in enumerate(statements):
            function_name = f"_stmt_{index}"
            functions.append(self._compiled_function(function_name, source))
            statement_table.append(f"    {source!r}: {function_name},")

        blocks.append("\n\n".join(functions))
        for variable, rows in (
            ("_EXPRESSIONS", expression_table), ("_LITERALS", literal_table),
            ("_STATEMENTS", statement_table), ("_SIGNAL_REFS", signal_refs),
        ):
            blocks.append(f"{variable} = {{\n" + "\n".join(rows) + "\n}" if rows else f"{variable} = {{}}")
        blocks.append(self._SEAM_LOOKUP.strip())
        return "\n\n\n".join(blocks)

    @classmethod
    def _literal_expression_value(cls, source: str) -> Any:
        """`source`'s own value if it is a plain Python literal (a bare
        `''`/`True`/`0`/... an env: default or a trigger constant routinely
        is), or _NOT_LITERAL — the sentinel that tells _compile_seam to
        compile a real function for it instead of inlining it."""
        try:
            return ast.literal_eval(source)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            return cls._NOT_LITERAL

    @classmethod
    def _roots(cls, source: str, mode: str) -> set[str]:
        """Every free name `source` reads — the root of an attribute chain
        (`source.tickets_sold.x` -> `source`) and any bare name, minus
        whatever the expression binds itself."""
        tree = ast.parse(source, mode=mode)
        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Lambda):
                bound |= {a.arg for a in node.args.args}
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for generator in node.generators:
                    bound |= {t.id for t in ast.walk(generator.target) if isinstance(t, ast.Name)}
        return {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        } - bound

    @classmethod
    def _referenced_attributes(cls, sources: list[str], root: str) -> set[str]:
        """Every `root.<attr>` this project's own expressions/statements
        actually read — what _namespace_class needs a property for, and
        nothing else: a platform-wide field no expression here reads
        (user.role, say, in a project that never touches it) never gets
        a property, and so never appears in the compiled file."""
        referenced: set[str] = set()
        for source in sources:
            tree = ast.parse(source, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == root:
                    referenced.add(node.attr)
        return referenced

    @classmethod
    def _namespace_class(cls, class_name: str, keys: list[str]) -> str:
        """A view over one of the scope's plain dicts, with one property per
        name this project declares.

        Properties rather than values copied in a constructor, deliberately:
        reading a key that is not there must raise where the expression reads
        it, exactly as the interpreted path does. A value copied up front
        would turn that into None, and quietly change what a boolean
        short-circuit does."""
        lines = [
            f"class {class_name}:",
            "    __slots__ = ('_v',)",
            "",
            "    def __init__(self, values):",
            "        object.__setattr__(self, '_v', values)",
        ]
        for key in keys:
            lines += ["", "    @property", f"    def {key}(self):", f"        return self._v[{key!r}]"]
        return "\n".join(lines)

    @classmethod
    def _compiled_function(cls, name: str, source: str) -> str:
        """`source` as a real function of the scope: one binding line per name
        it actually reads, then the text verbatim — which is the whole point
        of compiling it.

        Everything that reaches either primitive is an expression, never a
        statement: simpleeval only ever evaluates expressions, and the two
        callers that look like they pass a statement (an on-exit line, a task
        line) pass either a bare call or an assignment's right-hand side. So
        every generated function returns its value — a task line's JsSnippet
        reaches render_task_script exactly as before.

        The text is indented into a parenthesised return rather than inlined
        on one line, so a source spanning several lines, or carrying a
        trailing '#' comment, stays valid."""
        bindings = "\n".join(
            f"    {root} = {cls._ADAPTERS[root]}(_scope[{root!r}])" if root in cls._ADAPTERS
            else f"    {root} = _scope[{root!r}]"
            for root in sorted(cls._roots(source, "eval"))
        )
        indented = "\n".join(f"        {line}" for line in source.splitlines())
        return f"def {name}(_scope):\n" + (f"{bindings}\n" if bindings else "") + f"    return (\n{indented}\n    )\n"


# --- packaging ---------------------------------------------------------

def compile_package(project_path: Path, module_name: str, backend_src: Path) -> Path:
    """From a project directory or zip on disk — the CLI's own entry.
    No stored revision: nothing on disk has one."""
    contents = read_project_contents(project_path)
    if "index.yml" not in contents:
        raise CompileError(f"{project_path}: no index.yml at the top level — not a project.")
    return compile_contents(contents, module_name, backend_src)


def compile_contents(
    contents: dict[str, str | bytes], module_name: str, target_dir: Path, storage_revision: int | None = None,
) -> Path:
    """From the project's files already in hand — what the Build view
    uses, since a stored project lives in Archive rows, not on disk."""
    # Unlike a project's own keys, the package name is chosen by whoever
    # asks for the build: sanitizing it silently would only hide a typo.
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        raise CompileError(f"{module_name!r} is not usable as a Python package name.")
    if "index.yml" not in contents:
        raise CompileError("no index.yml among the project's files — not a project.")
    automaton = AutomatonBuilder().build(contents)

    module_source, prompt_source = Compiler(automaton, storage_revision).compile()
    package_dir = target_dir / module_name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / f"{module_name}.py").write_text(module_source, encoding="utf-8")
    (package_dir / "__init__.py").write_text(
        f"from .{module_name} import AUTOMATON, STORAGE_REVISION\n", encoding="utf-8",
    )
    (package_dir / "prompt.py").write_text(prompt_source, encoding="utf-8")

    data_dir = package_dir / "data"
    data_dir.mkdir(exist_ok=True)
    for rel_path, content in contents.items():
        if rel_path == "index.yml":
            # Already compiled into <module_name>.py and prompt.py in
            # full; a copy here would only be a stale duplicate of what
            # the package actually does.
            continue
        destination = data_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            destination.write_text(content, encoding="utf-8")
        else:
            destination.write_bytes(content)
    return package_dir


# --- verification --------------------------------------------------------

def _compare(what: str, compiled: Any, interpreted: Any) -> list[str]:
    return [] if compiled == interpreted else [f"{what}: compiled {compiled!r} != interpreted {interpreted!r}"]


def verify_package(project_path: Path, module_name: str) -> None:
    """Field-by-field against the same project built the ordinary way —
    including the answers derived from the expression text, which the
    compiled automaton computes through the very same code."""
    import importlib

    interpreted = AutomatonBuilder().build(read_project_contents(project_path))
    compiled = importlib.import_module(module_name).AUTOMATON

    problems: list[str] = []
    for attribute in (
        "project_id", "family", "project_revision", "project_ui_label", "project_ui_description",
        "talk_enabled", "autotracking_on_ai_message", "new_session_strategy", "general_prompt",
        "build_warnings",
    ):
        problems += _compare(attribute, getattr(compiled, attribute), getattr(interpreted, attribute))
    problems += _compare("init_action", compiled.init_action, interpreted.init_action)
    problems += _compare("signals", compiled.signals, interpreted.signals)
    problems += _compare("reactions", compiled.reactions, interpreted.reactions)
    problems += _compare("env_keys", compiled.env_keys, interpreted.env_keys)
    problems += _compare("sources", compiled.sources, interpreted.sources)
    problems += _compare(
        "general attachment names", sorted(compiled.general_attachments), sorted(interpreted.general_attachments),
    )
    problems += _compare("state keys", sorted(compiled.states), sorted(interpreted.states))
    for key, state in interpreted.states.items():
        if key not in compiled.states:
            continue
        problems += _compare(f"state {key!r}", compiled.states[key], state)
    problems += _compare(
        "declared_env_key_names", compiled.declared_env_key_names(), interpreted.declared_env_key_names(),
    )
    for key in interpreted.states:
        problems += _compare(
            f"triggerable_signal_names({key!r})",
            compiled.triggerable_signal_names(key), interpreted.triggerable_signal_names(key),
        )

    if problems:
        raise CompileError("compiled package does not match the interpreted automaton:\n  - " + "\n  - ".join(problems))
    print(
        f"OK — {len(interpreted.states)} states, "
        f"{sum(len(s.actions) for s in interpreted.states.values())} actions, "
        f"{len(interpreted.signals)} signals, {len(interpreted.sources)} sources, "
        f"{len(interpreted.env_keys)} env keys: identical to the interpreted automaton."
    )
