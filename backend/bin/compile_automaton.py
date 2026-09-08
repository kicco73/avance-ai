#!/usr/bin/env python3
"""Compiles one project's `index.yml` (+ its own archives) into a
standalone Python PACKAGE: a literal, per-project rewrite of ONLY the
`Automaton`/`State` classes, replacing the generic engine's dict/list/
interpreted-string shape with real attributes, real methods and real
Python control flow — states as attributes, actions as methods, triggers
as inlined `if`s, `env:`/on-exit as inlined assignments, `task:` posted
via an injected collaborator instead of persisted as text.

This is deliberately a NARROW replacement. Nothing else in the platform
is regenerated or rewritten: `chat_service.py`, `TrackingEngine`,
`TrackingProcessor`, the AI-provider/Reactions/Sources skills all stay
exactly the generic, feature-flag-composed platform code they already
are.

NOTE: this tool only WRITES the compiled package to disk (and, with
--verify, re-imports and structurally checks it). Nothing in main.py or
anywhere else in the live platform reads or switches on the generated
package yet — there is no runtime "load the compiled AUTOMATON instead
of building one from Archive rows" wiring in this patch. That is a
separate, not-yet-written change (see the open question about
reconciling this automaton's folded action contract — on-exit + task
posting inside one call, returning only the next state — with today's
3-step TrackingEngine/ProjectInspector.apply_manual_action sequence).

What gets compiled away, and why each one only ever existed to let one
engine run an unknown project:
  - `states: dict[str, State]`        -> literal attributes on AUTOMATON
  - `State.actions: list[Action]`     -> literal methods on the state
  - `Action.trigger` (simpleeval text) -> an inlined `if` in
    `evaluate_triggers`, verbatim source text, using the project's own
    real signal/env names
  - `Action.env`/`Action.on_exit` (parsed+evaluated per call) -> inlined
    `env.<key> = <expr>` assignments in the action method itself, in
    declaration order (env: dict first, then on-exit's own statements)
  - `Action.task` (re-parsed by _TaskEval on every run, and persisted as
    JSON text into a Job row a worker re-interprets, maybe in another
    process) -> a stable string id, resolved from TASKS — a plain dict —
    wherever the job actually runs; the action method only *posts* it
    (`tasks.post_now(task_id, **kwargs)`), it never runs inline
  - `scope: dict` passed as `names=` only to satisfy simpleeval's own
    calling convention -> real named parameters; nothing here interprets
    a string, so nothing needs a dict shape built for that purpose

What's deliberately NOT compiled, because its genericity has nothing to
do with running an unknown project:
  - `source`/`user`/`chat` — already real objects today, untouched
  - `env`'s own persistence (Db-backed key/value) — `env` here is a
    write-through view; whoever constructs it decides where a write goes
  - attachments/archive content (CSV text, etc.) — copied verbatim into
    the generated package's own `data/` directory, never parsed or
    embedded as Python literals; see --automaton-module below

Known, explicitly out of scope for this first version (raises a clear
CompileError naming the action, rather than silently emitting something
wrong): `task.defer(lambda: ..., when)` — hibernating a *fragment* of a
task script needs its own generated top-level function per defer site,
which needs its own design (see the RuntimeAutomaton design notes); a
task statement whose own return value must reach the frontend as a
JsSnippet (a bare `chat.*`-shaped result forwarded from *inside* task,
not on-exit) — none of the compiled project's own task lines produce
one, so nothing here threads a return value out of a task function at
all; a state, action, or signal/env name that isn't already a legal,
non-reserved Python identifier.

Usage:
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> [--backend-src DIR]
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> --verify

The input is either a project directory (index.yml at its top level, plus
its own archive files, in the same layout a real project's Archive rows
have) or a zip of that same layout — unpacked through the platform's own
project.archive.zip_importer.ZipImporter.extract_safely, the same
zip-slip-safe, __MACOSX-stripping, wrapper-folder-aware logic a real
project import already uses.

--automaton-module NAME writes the generated package to
<backend-src>/<NAME>/ (default backend-src: this tool's own
backend/src/, i.e. the same directory main.py lives in — a generated
package needs to sit there to be importable as a plain top-level
`import <NAME>`, if/when something in the platform is written to do
that import; this tool itself never imports it except under --verify).

--verify re-imports the freshly written package and checks it
STRUCTURALLY against the interpreted Automaton built from the same
project: every state present as an attribute, every action reachable as
a method, every generated method's source at least syntactically valid
Python (already guaranteed by writing it at all, but checked again,
explicitly, against the file as written on disk). It does not execute
any action body — a generic tool has no way to fabricate a `source`/
`user` object realistic enough for arbitrary project code to run against
safely; behavioral testing against real data belongs to a project-specific
harness (see the one written by hand for the Vueling Refund sample).
"""
from __future__ import annotations

import argparse
import keyword
import re
import sys
import tempfile
import zipfile
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

_BACKEND_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from automaton.automaton import Action, Automaton, State  # noqa: E402
from automaton.automaton_builder import AutomatonBuilder  # noqa: E402
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer  # noqa: E402
from project.archive.layout import BUNDLE_FILE_NAMES  # noqa: E402
from project.archive.zip_importer import ZipImporter  # noqa: E402

_EXCLUDED_FILENAMES = frozenset(BUNDLE_FILE_NAMES)

# Reserved on every generated State class — an action name colliding with
# one of these would silently shadow real automaton machinery instead of
# failing loudly, so it's checked and rejected at compile time instead.
_STATE_RESERVED_NAMES = frozenset({
    "key", "ui_label", "final", "chat_enabled", "fixed_message", "history_cutoff",
    "ai_may_read_sources", "ai_must_read_sources", "ai_may_write_sources", "evaluate_triggers",
})
# Same idea, for the automaton class itself — a state key must not collide
# with one of these or with another state's own uppercased constant name.
_AUTOMATON_RESERVED_NAMES = frozenset({
    "project_id", "project_revision", "talk_enabled", "autotracking_on_ai_message",
    "project_ui_label", "project_ui_description", "init_action_target",
    "get_state", "apply_manual_action", "run_task",
})
# Namespaces a task statement may legitimately reference — session/chat are
# excluded from the task view upstream already (EvaluationScope.for_task,
# IdentifierRegistry.TASK_SCOPE_EXCLUDES), so a task function never needs them.
_TASK_NAMESPACES = ("task", "signal", "env", "user", "source")


class CompileError(Exception):
    """A project construct this compiler cannot (yet, or ever) turn into
    literal Python — always raised with enough context (state/action name)
    to find the offending line in index.yml."""


# --- input handling (shared with the platform's own zip-import safety) ----

def _is_zip(path: Path) -> bool:
    return path.is_file() and (path.suffix.lower() == ".zip" or zipfile.is_zipfile(path))


def project_label(path: Path) -> str:
    return path.stem if _is_zip(path) else path.name


def read_project_contents(project_path: Path, exclude: frozenset[str] = _EXCLUDED_FILENAMES) -> dict[str, str | bytes]:
    if _is_zip(project_path):
        with tempfile.TemporaryDirectory(prefix="compile_automaton_") as staging:
            staging_dir = Path(staging)
            ZipImporter.extract_safely(project_path.read_bytes(), staging_dir)
            return _read_directory_contents(staging_dir, exclude)
    return _read_directory_contents(project_path, exclude)


def _read_directory_contents(project_dir: Path, exclude: frozenset[str]) -> dict[str, str | bytes]:
    contents: dict[str, str | bytes] = {}
    for path in sorted(project_dir.rglob("*")):
        if path.is_dir() or path.name in exclude:
            continue
        rel = path.relative_to(project_dir).as_posix()
        try:
            contents[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contents[rel] = path.read_bytes()
    return contents


# --- identifier safety -----------------------------------------------------
# Real projects routinely use state/action keys that aren't legal Python
# identifiers as they stand — `action-0` (the platform's own default name
# for an untitled action) and hyphenated state keys turn out to be the
# NORM, not the exception, in the sample projects this compiler was tried
# against. Rejecting them outright would make the tool nearly useless, so
# every raw key is sanitized into a Python-legal identifier instead
# (non-alnum -> `_`), with a uniqueness check so two different raw names
# can never quietly collide once sanitized. The raw string is never lost:
# `state.key` keeps it verbatim, and a `_ACTION_ATTR_BY_NAME`/
# `_STATE_ATTR_BY_KEY` table on each generated class translates an
# incoming raw name (from apply_manual_action / get_state, called with
# whatever string the frontend or a Tracking row actually holds) to the
# sanitized Python attribute that implements it.

_IDENTIFIER_INVALID = re.compile(r"[^0-9A-Za-z_]")


def _sanitize(raw: str) -> str:
    sanitized = _IDENTIFIER_INVALID.sub("_", raw)
    if sanitized[:1].isdigit():
        sanitized = f"_{sanitized}"
    if not sanitized or keyword.iskeyword(sanitized) or not sanitized.isidentifier():
        raise CompileError(f"{raw!r} cannot be turned into a usable Python identifier even after sanitizing.")
    return sanitized


def _sanitized_map(raw_names: list[str], reserved: frozenset[str], what: str) -> dict[str, str]:
    """raw name -> sanitized Python identifier, for every name in
    `raw_names`, checked against both `reserved` and each other."""
    result: dict[str, str] = {}
    seen: dict[str, str] = {}
    for raw in raw_names:
        sanitized = _sanitize(raw)
        if sanitized in reserved:
            raise CompileError(
                f"{what} {raw!r} sanitizes to {sanitized!r}, which collides with a name the compiled "
                "automaton itself needs — rename it in the project."
            )
        if sanitized in seen and seen[sanitized] != raw:
            raise CompileError(
                f"{what} {raw!r} and {seen[sanitized]!r} both sanitize to the same Python identifier "
                f"{sanitized!r} — rename one of them."
            )
        seen[sanitized] = raw
        result[raw] = sanitized
    return result


def _state_class_name(sanitized_key: str) -> str:
    return f"_State_{sanitized_key}"


def _state_const_name(sanitized_key: str) -> str:
    return f"STATE_{sanitized_key.upper()}"


def _task_function_name(sanitized_state_key: str, sanitized_action_name: str) -> str:
    return f"_task_{sanitized_state_key}_{sanitized_action_name}"


# --- action body: env:/on-exit inlined verbatim as assignments -------------

def _compile_env_and_on_exit(action: Action, context: str) -> list[str]:
    """Literal `env.<key> = <expr>` / `chat.<method>(...)` statement lines
    for `action`'s own `env:` dict (if any) followed by its `on-exit`
    script (if any) — same order eval_action_env/eval_action_on_exit run
    in today, just executed once, inline, instead of on every call."""
    lines: list[str] = []
    for key, expr in (action.env or {}).items():
        lines.append(f"env.{key} = {expr}")
    if action.on_exit:
        for _lineno, statement in TriggerExpressionAnalyzer.task_statements(action.on_exit):
            assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
            if assignment is not None:
                key, expr = assignment
                lines.append(f"env.{key} = {expr}")
                continue
            if TriggerExpressionAnalyzer.bare_namespace_call(statement, "chat") is not None:
                lines.append(statement)
                continue
            raise CompileError(
                f"{context}: on-exit statement is neither an `env.<key> = ...` assignment nor a bare "
                f"`chat.<method>(...)` call, which AutomatonValidator should already have rejected: {statement!r}"
            )
    return lines


# --- task: compiled into its own top-level function, resolved by id -------

def _compile_task(
    action: Action, sanitized_state_key: str, sanitized_action_name: str, context: str,
) -> tuple[str, str, list[str]]:
    """(source of the generated top-level task function, its own function
    name, list of param names it needs) for `action.task`. Raises
    CompileError on `task.defer(...)` — hibernating a lambda fragment
    needs its own per-defer-site generated function, not implemented
    here yet."""
    statements = TriggerExpressionAnalyzer.task_statements(action.task or "")
    body_lines: list[str] = []
    needed: set[str] = set()
    local_names: set[str] = set()
    for _lineno, statement in statements:
        # An assignment's own text (`name = task.prompt(...)`) is not a
        # valid mode="eval" expression on its own — namespace_calls/
        # namespace_refs must see just the RHS for that shape, the whole
        # statement only for a bare call.
        assignment = TriggerExpressionAnalyzer.task_assignment(statement)
        expr_to_scan = assignment[1] if assignment is not None else statement
        for method, _argc in TriggerExpressionAnalyzer.namespace_calls(expr_to_scan, "task"):
            if method == "defer":
                raise CompileError(
                    f"{context}: task.defer(...) is not supported by this compiler yet — hibernating a lambda "
                    "fragment needs a per-defer-site generated function of its own (see the RuntimeAutomaton "
                    "design notes on task persistence)."
                )
        if assignment is not None:
            name, expr = assignment
            if name in _TASK_NAMESPACES:
                raise CompileError(f"{context}: task assigns to {name!r}, which shadows a reserved namespace name.")
            body_lines.append(f"{name} = {expr}")
            needed |= set(TriggerExpressionAnalyzer.namespace_refs(expr).keys()) - local_names
            local_names.add(name)
        else:
            body_lines.append(statement)
            needed |= set(TriggerExpressionAnalyzer.namespace_refs(statement).keys()) - local_names
    params = [name for name in _TASK_NAMESPACES if name in needed]
    if not body_lines:
        body_lines = ["pass"]
    func_name = _task_function_name(sanitized_state_key, sanitized_action_name)
    header = f"def {func_name}({', '.join(f'{p}: Any' for p in params)}) -> None:"
    indented = "\n".join(f"    {line}" for line in body_lines)
    return f"{header}\n{indented}\n    return None\n", func_name, params


# --- one state -> one class -------------------------------------------------

def _compile_state(
    state: State, state_key_map: dict[str, str], tasks_out: dict[str, tuple[str, str]],
) -> str:
    state_key = state.key
    sanitized_key = state_key_map[state_key]
    class_name = _state_class_name(sanitized_key)
    action_map = _sanitized_map(
        [a.name for a in state.actions], _STATE_RESERVED_NAMES, f"action name in state {state_key!r}",
    )

    lines: list[str] = [f"class {class_name}:"]
    lines.append(f"    key = {state_key!r}")
    lines.append(f"    ui_label = {state.ui_label!r}")
    lines.append(f"    final = {state.final!r}")
    lines.append(f"    chat_enabled = {state.chat_enabled!r}")
    lines.append(f"    fixed_message = {state.fixed_message!r}")
    lines.append(f"    history_cutoff = {state.history_cutoff!r}")
    lines.append(f"    ai_may_read_sources: tuple[str, ...] = {tuple(state.ai_may_read_sources)!r}")
    lines.append(f"    ai_must_read_sources: tuple[str, ...] = {tuple(state.ai_must_read_sources)!r}")
    lines.append(f"    ai_may_write_sources: tuple[str, ...] = {tuple(state.ai_may_write_sources)!r}")
    # Raw action name (whatever the frontend/a Tracking row actually holds,
    # e.g. 'action-0') -> the sanitized method that implements it — the one
    # place apply_manual_action needs to translate a string it doesn't
    # control into a real Python attribute.
    attr_by_name = ", ".join(f"{raw!r}: {sanitized!r}" for raw, sanitized in action_map.items())
    lines.append(f"    _ACTION_ATTR_BY_NAME: dict[str, str] = {{{attr_by_name}}}")
    lines.append("")

    lines.append(
        "    def evaluate_triggers(self, env: Env, signal: Signals, user: Any, source: Any, chat: Any, "
        "tasks: TaskPoster) -> \"StateBase | None\":"
    )
    triggerable = [a for a in state.actions if a.trigger is not None]
    if not triggerable:
        lines.append("        return None")
    else:
        for action in triggerable:
            lines.append(f"        if {action.trigger}:")
            lines.append(f"            return self.{action_map[action.name]}(env, signal, user, source, chat, tasks)")
        lines.append("        return None")
    lines.append("")

    for action in state.actions:
        sanitized_action_name = action_map[action.name]
        context = f"state {state_key!r}, action {action.name!r}"
        lines.append(
            f"    def {sanitized_action_name}(self, env: Env, signal: Signals, user: Any, source: Any, chat: Any, "
            "tasks: TaskPoster) -> \"StateBase\":"
        )
        body = _compile_env_and_on_exit(action, context)
        if action.task:
            task_id = f"{state_key}::{action.name}"  # arbitrary dict key, raw is fine — never a Python attribute
            func_source, func_name, params = _compile_task(action, sanitized_key, sanitized_action_name, context)
            tasks_out[task_id] = (func_source, func_name)
            kwargs = ", ".join(f"{p}={p}" for p in params if p != "task")
            body.append(f"tasks.post_now({task_id!r}{', ' + kwargs if kwargs else ''})")
        body.append(f"return {_state_const_name(state_key_map[action.target])}")
        lines.extend(f"        {line}" for line in body)
        lines.append("")

    return "\n".join(lines)


# --- the whole module --------------------------------------------------------

_HEADER = '''"""COMPILED automaton for project {project_id!r} @ revision {revision!r}.
GENERATED by backend/bin/compile_automaton.py from index.yml — DO NOT EDIT.
Structural shape only: archive/attachment content lives beside this file,
under data/ (see the package this module belongs to), read exactly as
before through `source`, never embedded here."""
from __future__ import annotations

from typing import Any, Callable, Protocol


class TaskPoster(Protocol):
    def post_now(self, task_id: str, **kwargs: Any) -> None: ...


class StateBase(Protocol):
    key: str
    ui_label: str
    final: bool
    chat_enabled: bool
    fixed_message: str | None
    history_cutoff: bool
    ai_may_read_sources: tuple[str, ...]
    ai_must_read_sources: tuple[str, ...]
    ai_may_write_sources: tuple[str, ...]

    def evaluate_triggers(
        self, env: "Env", signal: "Signals", user: Any, source: Any, chat: Any, tasks: TaskPoster,
    ) -> "StateBase | None": ...
'''


_ENV_DOCSTRING = (
    '    """This project\'s own env keys as real attributes. Assigning one\n'
    "    (`env.<key> = ...`, the on-exit text unchanged) both updates the\n"
    "    value in place and forwards the write to `_on_write` -- the\n"
    '    platform\'s own persistence call, injected once per turn."""\n'
)

_SIGNALS_DOCSTRING = (
    '    """This turn\'s validated signal values, as real read-only\n'
    "    attributes -- coercion against each signal's own declared type\n"
    "    still happens upstream, in the AI classification step this\n"
    '    module never touches."""\n'
)


def _compile_env_class(env_keys: list[str]) -> str:
    slots_repr = repr((*env_keys, "_on_write"))
    params = ", ".join(f'{k}: str = ""' for k in env_keys)
    assigns = "\n".join(f'        object.__setattr__(self, {k!r}, {k})' for k in env_keys) or "        pass"
    on_write_param = 'on_write: "Callable[[str, Any], None] | None" = None'
    init_params = on_write_param + (f", {params}" if params else "")
    return (
        "class Env:\n"
        + _ENV_DOCSTRING
        + f"    __slots__ = {slots_repr}\n\n"
        + f"    def __init__(self, *, {init_params}) -> None:\n"
        + "        object.__setattr__(self, '_on_write', on_write)\n"
        + f"{assigns}\n\n"
        + "    def __setattr__(self, name: str, value: Any) -> None:\n"
        + "        object.__setattr__(self, name, value)\n"
        + "        if self._on_write is not None:\n"
        + "            self._on_write(name, value)\n"
    )


def _compile_signals_class(signal_names: list[str]) -> str:
    slots_repr = repr(tuple(signal_names))
    params = ", ".join(f'{n}: Any = None' for n in signal_names)
    init_signature = f"self, *, {params}" if params else "self"
    assigns = "\n".join(f"        self.{n} = {n}" for n in signal_names) or "        pass"
    return (
        "class Signals:\n"
        + _SIGNALS_DOCSTRING
        + f"    __slots__ = {slots_repr}\n\n"
        + f"    def __init__({init_signature}) -> None:\n"
        + f"{assigns}\n"
    )


def compile_module(automaton: Automaton) -> str:
    # `states[""]` is AutomatonBuilder's own synthetic pseudo-state — key
    # "", holding only `init_action` (see automaton_builder.py: `states[""]
    # = State(key="", ..., actions=[init_action])`), never a real
    # destination/dispatch target: chat_service reaches init_action
    # directly (automaton.init_action), never through get_state(""). Not a
    # legal Python identifier either way, and not needed here.
    real_states = {key: state for key, state in automaton.states.items() if key != ""}

    state_key_map = _sanitized_map(list(real_states.keys()), _AUTOMATON_RESERVED_NAMES, "state key")
    const_names = {raw: _state_const_name(sanitized) for raw, sanitized in state_key_map.items()}
    if len(set(const_names.values())) != len(const_names):
        raise CompileError("two state keys collide once uppercased into a module-level constant name.")

    env_keys = sorted(k.name for k in automaton.env_keys)
    signal_names = sorted(s.name for s in automaton.signals)

    parts: list[str] = [
        _HEADER.format(project_id=automaton.project_id, revision=getattr(automaton, "revision", None)),
        _compile_env_class(env_keys),
        _compile_signals_class(signal_names),
    ]

    tasks: dict[str, tuple[str, str]] = {}
    state_class_source: list[str] = []
    singleton_lines: list[str] = []
    for state_key, state in real_states.items():
        state_class_source.append(_compile_state(state, state_key_map, tasks))
        singleton_lines.append(f"{const_names[state_key]} = {_state_class_name(state_key_map[state_key])}()")
    parts.append("\n\n".join(state_class_source))
    parts.append("\n".join(singleton_lines))

    if tasks:
        task_sources = "\n\n".join(source for source, _func_name in tasks.values())
        registry = ",\n".join(f"    {task_id!r}: {func_name}" for task_id, (_source, func_name) in tasks.items())
        parts.append(task_sources)
        parts.append(f"TASKS: dict[str, Callable[..., None]] = {{\n{registry},\n}}")
    else:
        parts.append("TASKS: dict[str, Callable[..., None]] = {}")

    # Raw state key (whatever a Tracking row / the frontend actually holds)
    # -> the sanitized attribute implementing it — get_state's own
    # translation table, the automaton-level twin of each state's own
    # _ACTION_ATTR_BY_NAME.
    state_attr_by_key = ", ".join(f"{raw!r}: {sanitized!r}" for raw, sanitized in state_key_map.items())
    automaton_attrs = "\n".join(f"    {sanitized} = {const_names[raw]}" for raw, sanitized in state_key_map.items())
    parts.append(f'''class {_automaton_class_name(automaton.project_id)}:
    project_id = {automaton.project_id!r}
    project_revision = {getattr(automaton, "revision", None)!r}
    project_ui_label = {automaton.project_ui_label!r}
    project_ui_description = {automaton.project_ui_description!r}
    talk_enabled = {automaton.talk_enabled!r}
    autotracking_on_ai_message = {automaton.autotracking_on_ai_message!r}
    init_action_target = {const_names[automaton.init_action.target]}

{automaton_attrs}

    _STATE_ATTR_BY_KEY: dict[str, str] = {{{state_attr_by_key}}}

    def get_state(self, state_key: str) -> StateBase:
        attr = self._STATE_ATTR_BY_KEY.get(state_key)
        if attr is None:
            raise ValueError(f"Unknown state {{state_key!r}}")
        return getattr(self, attr)

    def apply_manual_action(
        self, state_key: str, action_name: str, env: "Env", signal: "Signals", user: Any, source: Any,
        chat: Any, tasks: TaskPoster,
    ) -> StateBase:
        state = self.get_state(state_key)
        attr = state._ACTION_ATTR_BY_NAME.get(action_name)
        if attr is None:
            raise ValueError(f"Action {{action_name!r}} not available in state {{state_key!r}}")
        return getattr(state, attr)(env, signal, user, source, chat, tasks)

    def run_task(self, task_id: str, **kwargs: Any) -> None:
        TASKS[task_id](**kwargs)


AUTOMATON = {_automaton_class_name(automaton.project_id)}()
''')
    return "\n\n".join(parts) + "\n"


def _automaton_class_name(project_id: str) -> str:
    return "".join(part.capitalize() for part in project_id.replace("-", "_").split("_")) + "Automaton"


# --- packaging: generated module + copied project data ---------------------

def compile_package(project_path: Path, module_name: str, backend_src: Path) -> Path:
    # Unlike a project's own state/action keys, the package name is
    # chosen by whoever runs this tool — sanitizing it silently would just
    # hide a typo, so this one is checked, never auto-fixed.
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        raise CompileError(f"--automaton-module {module_name!r} is not usable as a Python package name.")
    contents = read_project_contents(project_path)
    if "index.yml" not in contents:
        raise CompileError(f"{project_path}: no index.yml at the top level — not a project.")
    automaton = AutomatonBuilder().build(contents)

    package_dir = backend_src / module_name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(compile_module(automaton), encoding="utf-8")

    data_dir = package_dir / "data"
    data_dir.mkdir(exist_ok=True)
    for rel_path, content in contents.items():
        if rel_path == "index.yml":
            # Already fully compiled into __init__.py above (states, actions,
            # triggers, env, on-exit, task — everything). Nothing at runtime
            # ever reads it back from here once the compiled package is in
            # use, so keeping a copy would just be a stale, misleading
            # duplicate of what __init__.py actually does.
            continue
        dest = data_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            dest.write_text(content, encoding="utf-8")
        else:
            dest.write_bytes(content)
    return package_dir


# --- structural verification ------------------------------------------------

def verify_package(project_path: Path, module_name: str) -> None:
    import importlib

    contents = read_project_contents(project_path)
    automaton = AutomatonBuilder().build(contents)
    compiled = importlib.import_module(module_name)
    compiled_automaton = compiled.AUTOMATON
    real_states = {key: state for key, state in automaton.states.items() if key != ""}

    for state_key, state in real_states.items():
        compiled_state = compiled_automaton.get_state(state_key)
        assert compiled_state.key == state_key, f"state {state_key}: key mismatch"
        assert compiled_state.final == state.final, f"state {state_key}: final mismatch"
        assert compiled_state.chat_enabled == state.chat_enabled, f"state {state_key}: chat_enabled mismatch"
        assert compiled_state.fixed_message == state.fixed_message, f"state {state_key}: fixed_message mismatch"
        for action in state.actions:
            attr = compiled_state._ACTION_ATTR_BY_NAME.get(action.name)
            method = getattr(compiled_state, attr, None) if attr is not None else None
            assert callable(method), f"state {state_key}: action {action.name!r} missing on compiled state"
        # every raw action name this state declares is reachable through
        # the translation table, and nothing extra leaked into it
        assert set(compiled_state._ACTION_ATTR_BY_NAME.keys()) == {a.name for a in state.actions}, (
            f"state {state_key}: compiled action set {set(compiled_state._ACTION_ATTR_BY_NAME.keys())} != "
            f"{[a.name for a in state.actions]}"
        )
    assert set(compiled_automaton._STATE_ATTR_BY_KEY.keys()) == set(real_states), "state set mismatch"
    print(f"OK — {len(real_states)} states, "
          f"{sum(len(s.actions) for s in real_states.values())} actions, structurally verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="Project directory or zip file")
    parser.add_argument(
        "--automaton-module", required=True, dest="module_name",
        help="Name of the generated top-level package (written under --backend-src)",
    )
    parser.add_argument(
        "--backend-src", type=Path, default=_BACKEND_SRC,
        help="Directory to write the generated package into (default: this tool's own backend/src/)",
    )
    parser.add_argument("--verify", action="store_true", help="Structurally verify an already-compiled package")
    args = parser.parse_args()

    try:
        if args.verify:
            verify_package(args.project, args.module_name)
        else:
            package_dir = compile_package(args.project, args.module_name, args.backend_src)
            print(f"Wrote {package_dir}")
    except CompileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
