"""Compiles one project (its index.yml plus its own archives) into a
standalone Python package: the same automaton, built by literal Python
constructor calls instead of by parsing YAML at boot.

What the generated package contains:

    <name>/__init__.py   every State/Action/Signal/Reaction/EnvKey/Source
                         as a literal, and AUTOMATON, an instance of a
                         CompiledAutomaton composed from automaton.core's
                         CoreAutomaton plus the platform mixins this
                         product enables
    <name>/prompt.py     every AI-facing text as a named constant —
                         general_prompt, each state's contextual_prompt,
                         each signal's and reaction's definition, each
                         env key's and source's ai_definition
    <name>/data/         the project's own archive files, verbatim, read
                         back at import time through the platform's own
                         ArchiveResolver

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

import dataclasses
import keyword
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import ast
from automaton.automaton_builder import AutomatonBuilder
from automaton.identifier_registry import IdentifierRegistry
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.model import Action, EnvKey, MemoryArchive, Reaction, Signal, Source, State
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


# --- prompt.py -------------------------------------------------------------
# Which dataclass field on which type holds text written for the model.
# Everything named here is emitted as a constant in prompt.py and
# referenced from __init__.py; everything else is emitted inline.
# fixed_message is deliberately NOT here: it is a canned reply handed to
# the user verbatim, not something the model reads.
_PROMPT_FIELDS: dict[type, tuple[str, ...]] = {
    State: ("contextual_prompt",),
    Signal: ("definition",),
    Reaction: ("definition",),
    EnvKey: ("ai_definition",),
    Source: ("ai_definition",),
}


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

    def render(self, project_id: str) -> str:
        header = (
            f'"""Every text the model reads in project {project_id!r}.\n\n'
            "GENERATED by backend/bin/compile_automaton.py from index.yml.\n"
            "Recompiling overwrites this file. Nothing enforces that, and\n"
            "nothing else in the package depends on how these read — they\n"
            "are values, not behaviour.\"\"\"\n"
        )
        body = "\n".join(f"{name} = {value!r}\n" for name, value in sorted(self._by_name.items()))
        return header + "\n" + (body or "# This project declares no model-facing text at all.\n")


# --- literal emission, driven by dataclasses.fields() ----------------------

def _literal(value: Any) -> str:
    """A repr Python can read back. Only the shapes the model actually
    holds — anything else is a field this compiler has never seen and
    must be looked at rather than guessed at."""
    if isinstance(value, MemoryArchive):
        # Never inlined: the bytes live in data/ and come back through
        # ArchiveResolver at import time (see _ARCHIVES in the preamble).
        return f"_ARCHIVES[{value.filename!r}]"
    if value is None or isinstance(value, (str, bool, int, float)):
        return repr(value)
    if isinstance(value, tuple):
        return "(" + "".join(f"{_literal(v)}, " for v in value) + ")"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_literal(k)}: {_literal(v)}" for k, v in value.items()) + "}"
    if dataclasses.is_dataclass(value):
        return _dataclass_literal(value)
    raise CompileError(
        f"cannot emit a literal for {type(value).__name__} ({value!r}) — a new field shape this compiler "
        "has not been taught about."
    )


def _dataclass_literal(obj: Any, prompts: PromptTable | None = None, key: str | None = None) -> str:
    """`Type(field=<literal>, ...)` for every field the dataclass
    declares — read from dataclasses.fields(), so a field added upstream
    is emitted rather than quietly lost. Fields listed in _PROMPT_FIELDS
    are emitted as a reference into prompt.py instead of inline."""
    prompt_fields = _PROMPT_FIELDS.get(type(obj), ()) if prompts is not None else ()
    parts = []
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if field.name in prompt_fields and isinstance(value, str) and value:
            prefix = type(obj).__name__.upper()
            parts.append(f"{field.name}=prompt.{prompts.add(prefix, key or '', field.name, value)}")
        else:
            parts.append(f"{field.name}={_literal(value)}")
    return f"{type(obj).__name__}({', '.join(parts)})"


def _state_literal(state: State, prompts: PromptTable) -> str:
    """Like _dataclass_literal, but each action is emitted on its own
    line: a state with a dozen actions is otherwise one unreadable line."""
    parts = []
    for field in dataclasses.fields(state):
        value = getattr(state, field.name)
        if field.name == "actions":
            actions = "".join(f"\n        {_dataclass_literal(a, prompts, key=f'{state.key}.{a.name}')}," for a in value)
            parts.append(f"    actions=[{actions}\n    ]")
        elif field.name in _PROMPT_FIELDS[State] and isinstance(value, str) and value:
            parts.append(f"    {field.name}=prompt.{prompts.add('STATE', state.key, field.name, value)}")
        else:
            parts.append(f"    {field.name}={_literal(value)}")
    return "State(\n" + ",\n".join(parts) + ",\n)"


_PREAMBLE = '''"""COMPILED automaton for project {project_id!r}.

GENERATED by backend/bin/compile_automaton.py from index.yml — DO NOT EDIT.

The project's structure is literal Python here; its model-facing texts
are in prompt.py beside this file; its archive files are in data/, read
back through the platform's own ArchiveResolver rather than inlined.

Behaviour is still inherited: this automaton evaluates triggers, `env:`,
on-exit and task through CoreAutomaton's own seams, on the text each
Action carries."""
from __future__ import annotations

from pathlib import Path

from automaton.builder.archive_resolver import ArchiveResolver
from automaton.core import CoreAutomaton
from logging_factory import LoggerFactory
from automaton.introspection import IntrospectionMixin
from automaton.model import Action, EnvKey, Reaction, Signal, Source, State
from automaton.payloads import PayloadsMixin

from . import prompt

_logger = LoggerFactory.get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"


def _read_data_dir() -> dict:
    contents: dict = {{}}
    for path in sorted(_DATA_DIR.rglob("*")):
        if path.is_dir():
            continue
        name = path.relative_to(_DATA_DIR).as_posix()
        try:
            contents[name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contents[name] = path.read_bytes()
    return contents


# Same conversion the interpreted path uses, on the same files: whether
# an archive is text or base64, and its media type, is decided in exactly
# one place for both.
_ARCHIVES = ArchiveResolver.convert_contents_to_archives(_read_data_dir())
'''

_AUTOMATON_CLASS = '''
class CompiledAutomaton(CoreAutomaton, PayloadsMixin, IntrospectionMixin):
    """This product's automaton: CoreAutomaton plus the platform contracts
    it enables. A product that serves no design view drops PayloadsMixin,
    one with no metrics or Inspector drops IntrospectionMixin — the base
    class alone is enough to run a chat."""
{seam_overrides}

AUTOMATON = CompiledAutomaton(
    init_action=_INIT_ACTION,
    states=_STATES,
    general_prompt=prompt.GENERAL_PROMPT,
    signals=_SIGNALS,
    attachments=_ARCHIVES,
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
'''


def compile_module(automaton: Any) -> tuple[str, str]:
    """(source of __init__.py, source of prompt.py)."""
    prompts = PromptTable()
    prompts.add_named("GENERAL_PROMPT", automaton.general_prompt or "")

    parts: list[str] = [_PREAMBLE.format(project_id=automaton.project_id)]

    state_names: dict[str, str] = {}
    state_blocks: list[str] = []
    for index, (key, state) in enumerate(automaton.states.items()):
        name = f"_STATE_{index}"
        state_names[key] = name
        state_blocks.append(f"# state {key!r}\n{name} = {_state_literal(state, prompts)}")
    parts.append("\n\n".join(state_blocks))
    parts.append(
        "_STATES = {\n"
        + "".join(f"    {key!r}: {state_names[key]},\n" for key in automaton.states)
        + "}"
    )

    parts.append(f"_INIT_ACTION = {_dataclass_literal(automaton.init_action, prompts, key='init')}")
    for attribute, variable, prefix in (
        ("signals", "_SIGNALS", "SIGNAL"),
        ("reactions", "_REACTIONS", "REACTION"),
        ("env_keys", "_ENV_KEYS", "ENVKEY"),
        ("sources", "_SOURCES", "SOURCE"),
    ):
        items = getattr(automaton, attribute)
        rendered = "".join(f"    {_dataclass_literal(i, prompts, key=i.name)},\n" for i in items)
        parts.append(f"{variable} = [\n{rendered}]" if items else f"{variable} = []")

    # Keyed by the name the project declared, which is not necessarily the
    # archive's own path: ArchiveResolver resolves a bare 'general_prompt.txt'
    # against a file actually stored at 'behaviour/general_prompt.txt'. The
    # value is looked up by that real path (see _literal for MemoryArchive).
    parts.append(
        "_GENERAL_ATTACHMENTS = {\n"
        + "".join(
            f"    {name!r}: {_literal(archive)},\n"
            for name, archive in automaton.general_attachments.items()
        )
        + "}"
    )
    parts.append(compile_seam(automaton))
    parts.append(_AUTOMATON_CLASS.format(
        seam_overrides=_SEAM_OVERRIDES,
        autotracking=automaton.autotracking_on_ai_message,
        project_id=automaton.project_id,
        family=automaton.family,
        project_revision=automaton.project_revision,
        ui_label=automaton.project_ui_label,
        ui_description=automaton.project_ui_description,
        talk_enabled=automaton.talk_enabled,
        new_session_strategy=automaton.new_session_strategy,
        build_warnings=automaton.build_warnings,
    ))
    return "\n\n".join(parts) + "\n", prompts.render(automaton.project_id)


# --- packaging -------------------------------------------------------------

def _refuse_index_yml_as_attachment(automaton: Any) -> None:
    """index.yml is compiled away in full and never copied into data/, so
    a project that hands it to the model as an attachment would silently
    lose it. Nothing does today; if something ever does, it must be a
    build error rather than a missing file at run time."""
    declaring = [
        where for where, names in
        [("the project itself", automaton.general_attachments)]
        + [(f"signal {s.name!r}", s.attachments) for s in automaton.signals]
        + [(f"state {key!r}", state.attachments) for key, state in automaton.states.items()]
        + [
            (f"action {action.name!r} of state {key!r}", action.attachments)
            for key, state in automaton.states.items() for action in state.actions
        ]
        if "index.yml" in names
    ]
    if declaring:
        raise CompileError(
            "index.yml is declared as an attachment by " + ", ".join(declaring)
            + " — a compiled package does not ship it, since it is compiled away in full."
        )


def compile_package(project_path: Path, module_name: str, backend_src: Path) -> Path:
    """From a project directory or zip on disk — the CLI's own entry."""
    contents = read_project_contents(project_path)
    if "index.yml" not in contents:
        raise CompileError(f"{project_path}: no index.yml at the top level — not a project.")
    return compile_contents(contents, module_name, backend_src)


def compile_contents(contents: dict[str, str | bytes], module_name: str, target_dir: Path) -> Path:
    """From the project's files already in hand — what the Build view
    uses, since a stored project lives in Archive rows, not on disk."""
    # Unlike a project's own keys, the package name is chosen by whoever
    # asks for the build: sanitizing it silently would only hide a typo.
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        raise CompileError(f"{module_name!r} is not usable as a Python package name.")
    if "index.yml" not in contents:
        raise CompileError("no index.yml among the project's files — not a project.")
    automaton = AutomatonBuilder().build(contents)
    _refuse_index_yml_as_attachment(automaton)

    module_source, prompt_source = compile_module(automaton)
    package_dir = target_dir / module_name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(module_source, encoding="utf-8")
    (package_dir / "prompt.py").write_text(prompt_source, encoding="utf-8")

    data_dir = package_dir / "data"
    data_dir.mkdir(exist_ok=True)
    for rel_path, content in contents.items():
        if rel_path == "index.yml":
            # Already compiled into __init__.py and prompt.py in full;
            # a copy here would only be a stale duplicate of what the
            # package actually does.
            continue
        destination = data_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            destination.write_text(content, encoding="utf-8")
        else:
            destination.write_bytes(content)
    return package_dir


# --- verification ----------------------------------------------------------

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
    # index.yml aside: the interpreted automaton carries it in `attachments`
    # only because convert_contents_to_archives is handed the whole project,
    # and the compiled package deliberately does not ship it (it has been
    # compiled away in full). compile_package refuses to compile a project
    # that actually declares it as an attachment, so this is the only
    # difference between the two, and it is one nothing reads.
    problems += _compare(
        "attachment names",
        sorted(compiled.attachments), sorted(set(interpreted.attachments) - {"index.yml"}),
    )
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


# --- the seam, compiled ----------------------------------------------------
# Everything below turns the expression text an Action carries into real
# Python, so a compiled automaton never evaluates a string. It replaces
# exactly the three primitives CoreAutomaton isolates for the purpose —
# _evaluate_expression, _evaluate_statement, _referenced_signal_names — and
# nothing else: every loop, every try/except, every warning around them
# stays the platform's own code, so an interpreted and a compiled automaton
# cannot drift apart on ordering, on what a failure means, or on what is
# collected and what is skipped.

# The only scope entries that are plain dicts (see tracking/
# evaluation_scope.py). An expression reads them as attributes thanks to
# simpleeval's attribute-to-item fallback, so a compiled module needs a
# real object with those attributes for `signal.progress` to stay
# `signal.progress`. Everything else in the scope is already an object and
# is bound straight through.
_ADAPTERS = {"signal": "_Signals", "env": "_Env", "user": "_User"}


def _roots(source: str, mode: str) -> set[str]:
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


def _collect_sources(automaton: Any) -> tuple[list[str], list[str]]:
    """(expressions, statements) — every distinct piece of text this
    automaton would otherwise hand to an evaluator, split by which
    primitive receives it. In encounter order, so a regenerated module
    diffs cleanly against the one before it."""
    expressions: dict[str, None] = {}
    statements: dict[str, None] = {}
    actions = [automaton.init_action] + [a for state in automaton.states.values() for a in state.actions]
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


def _namespace_class(class_name: str, docstring: str, keys: list[str]) -> str:
    """A view over one of the scope's plain dicts, with one property per
    name this project declares.

    Properties rather than values copied in a constructor, deliberately:
    reading a key that is not there must raise where the expression reads
    it, exactly as the interpreted path does. A value copied up front
    would turn that into None, and quietly change what a boolean
    short-circuit does."""
    lines = [
        f"class {class_name}:",
        f'    """{docstring}"""',
        "    __slots__ = ('_v',)",
        "",
        "    def __init__(self, values):",
        "        object.__setattr__(self, '_v', values)",
    ]
    for key in keys:
        lines += ["", "    @property", f"    def {key}(self):", f"        return self._v[{key!r}]"]
    if not keys:
        lines += ["", "    # This project declares none."]
    return "\n".join(lines)


def _compiled_function(name: str, source: str) -> str:
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
        f"    {root} = {_ADAPTERS[root]}(_scope[{root!r}])" if root in _ADAPTERS
        else f"    {root} = _scope[{root!r}]"
        for root in sorted(_roots(source, "eval"))
    )
    indented = "\n".join(f"        {line}" for line in source.splitlines())
    return f"def {name}(_scope):\n" + (f"{bindings}\n" if bindings else "") + f"    return (\n{indented}\n    )\n"


_SEAM_LOOKUP = '''
def _compiled(table, text, kind):
    """The compiled callable for `text`, or a loud failure. A miss means
    this package was generated from a different revision of the project
    than the one that produced the text — the seams above would otherwise
    swallow it as an ordinary evaluation failure, so it is logged as the
    configuration error it is before it gets there."""
    compiled = table.get(text)
    if compiled is None:
        _logger.error(
            "COMPILED AUTOMATON: no compiled %s for %r — this package is out of sync "
            "with the project it was built from.", kind, text,
        )
        raise KeyError(text)
    return compiled
'''

_SEAM_OVERRIDES = '''
    # --- the seam ---------------------------------------------------------
    # The three primitives CoreAutomaton isolates, and nothing else. Every
    # loop, every try/except and every warning around them is inherited
    # unchanged, which is what keeps this automaton's behaviour identical
    # to the interpreted one rather than merely similar.

    @classmethod
    def _evaluate_expression(cls, expression, scope):
        return _compiled(_EXPRESSIONS, expression, "expression")(scope)

    @classmethod
    def _evaluate_statement(cls, statement, scope):
        return _compiled(_STATEMENTS, statement, "statement")(scope)

    @classmethod
    def _referenced_signal_names(cls, expression):
        return _SIGNAL_REFS[expression]
'''


def compile_seam(automaton: Any) -> str:
    """The whole compiled-behaviour section of __init__.py."""
    expressions, statements = _collect_sources(automaton)
    blocks = [
        _namespace_class(
            "_Signals", "This project's declared signals, as real attributes.",
            [signal.name for signal in automaton.signals],
        ),
        _namespace_class(
            "_Env", "This project's declared env variables, as real attributes.",
            [env_key.name for env_key in automaton.env_keys],
        ),
        _namespace_class(
            "_User", "The user fields an expression may read, as real attributes.",
            sorted(IdentifierRegistry.USER),
        ),
    ]

    functions: list[str] = []
    expression_table: list[str] = []
    statement_table: list[str] = []
    signal_refs: list[str] = []
    for index, source in enumerate(expressions):
        function_name = f"_expr_{index}"
        functions.append(_compiled_function(function_name, source))
        expression_table.append(f"    {source!r}: {function_name},")
        signal_refs.append(f"    {source!r}: frozenset({sorted(TriggerExpressionAnalyzer.signal_names(source))!r}),")
    for index, source in enumerate(statements):
        function_name = f"_stmt_{index}"
        functions.append(_compiled_function(function_name, source))
        statement_table.append(f"    {source!r}: {function_name},")

    blocks.append("\n\n".join(functions) if functions else "# This project has nothing to evaluate.")
    for variable, rows in (
        ("_EXPRESSIONS", expression_table), ("_STATEMENTS", statement_table), ("_SIGNAL_REFS", signal_refs),
    ):
        blocks.append(f"{variable} = {{\n" + "\n".join(rows) + "\n}" if rows else f"{variable} = {{}}")
    blocks.append(_SEAM_LOOKUP.strip())
    return "\n\n\n".join(blocks)
