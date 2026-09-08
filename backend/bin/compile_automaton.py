#!/usr/bin/env python3
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

THIS STEP COMPILES THE DATA, NOT YET THE BEHAVIOUR. The generated
automaton inherits CoreAutomaton's four seams unchanged, so triggers,
`env:`, on-exit and task still run through simpleeval on the text each
Action carries. Replacing those four with literal Python is the next
step; the point of this one is a compiled package that actually boots
the platform and answers identically to the interpreted automaton.

The emission is driven by dataclasses.fields(), never by a hand-written
list of field names: a field added to State or Action tomorrow is
emitted automatically instead of being silently dropped.

Usage:
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> [--backend-src DIR]
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> --verify

--verify re-imports the package it just wrote and compares it, field by
field, against the automaton built from the same project by the ordinary
AutomatonBuilder: every state, every action, every signal, source, env
key and reaction, plus the derived answers (declared env key names, the
triggerable signal names of each state).
"""
from __future__ import annotations

import argparse
import dataclasses
import keyword
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

_BACKEND_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from automaton.automaton_builder import AutomatonBuilder  # noqa: E402
from automaton.model import Action, EnvKey, MemoryArchive, Reaction, Signal, Source, State  # noqa: E402
from project.archive.layout import BUNDLE_FILE_NAMES  # noqa: E402
from project.archive.zip_importer import ZipImporter  # noqa: E402

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
from automaton.introspection import IntrospectionMixin
from automaton.model import Action, EnvKey, Reaction, Signal, Source, State
from automaton.payloads import PayloadsMixin

from . import prompt

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
    parts.append(_AUTOMATON_CLASS.format(
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
    # Unlike a project's own keys, the package name is chosen by whoever
    # runs this tool: sanitizing it silently would only hide a typo.
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        raise CompileError(f"--automaton-module {module_name!r} is not usable as a Python package name.")
    contents = read_project_contents(project_path)
    if "index.yml" not in contents:
        raise CompileError(f"{project_path}: no index.yml at the top level — not a project.")
    automaton = AutomatonBuilder().build(contents)
    _refuse_index_yml_as_attachment(automaton)

    module_source, prompt_source = compile_module(automaton)
    package_dir = backend_src / module_name
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
    parser.add_argument("--verify", action="store_true", help="Check an already-compiled package against its project")
    args = parser.parse_args()

    try:
        if args.verify:
            verify_package(args.project, args.module_name)
        else:
            print(f"Wrote {compile_package(args.project, args.module_name, args.backend_src)}")
    except CompileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
