"""source.attachment(name): one of the current project's own stored
files, read straight from Db — never automaton.attachments' own
in-memory copy (see Automaton.set_storage_location). That in-memory
dict is built eagerly, on every automaton build, for every single file
the project has, whether anything ever references it or not; reading
through Db here instead means a large file nothing has declared under
its own `attachments:` is only ever loaded — and only the one file
actually named — the moment a trigger/env: expression asks for it."""
from __future__ import annotations

from pathlib import Path

from automaton.automaton import Automaton
from db import Db


def _resolve_archive_name(db: Db, project_name: str, revision: int, name: str) -> str:
    """Same exact/unique-basename matching build-time `attachments:`
    declarations get (AutomatonBuilder._extract_required_archives) —
    reimplemented here against Db.list_archives (names only, no content)
    rather than an in-memory archive dict, so resolving the name never
    itself requires loading every file's content either."""
    names = db.list_archives(project_name, revision=revision)
    if name in names:
        return name
    matches = [n for n in names if Path(n).name == name]
    if len(matches) > 1:
        raise ValueError(f"source.attachment('{name}') is ambiguous — matches {', '.join(sorted(matches))}")
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"source.attachment('{name}') not found in project '{project_name}'.")


def read(db: Db, automaton: Automaton, name: str) -> str:
    """Raises ValueError — caught and logged by automaton.Automaton's own
    trigger/env: evaluation (see eval_action_env/_eval_trigger), same as
    any other bad scope reference — for an automaton with no known
    storage location (never built through AutomatonLoader/ProjectManager,
    e.g. a validation-only build — see set_storage_location), a name that
    doesn't resolve to exactly one file, or one whose stored content_type
    isn't text/* (an image/PDF/etc. — no binary-to-text extraction exists
    in this codebase)."""
    if automaton.project_name is None or automaton.revision is None:
        raise ValueError(f"source.attachment('{name}'): this automaton has no known storage location to read from.")
    archive_name = _resolve_archive_name(db, automaton.project_name, automaton.revision, name)
    content_type = db.get_archive_content_type(automaton.project_name, archive_name, revision=automaton.revision)
    if content_type is None or not content_type.startswith("text/"):
        raise ValueError(
            f"source.attachment('{name}') resolved to '{archive_name}', "
            f"a binary file ({content_type or 'unknown type'}) — only text files can be read this way."
        )
    content = db.get_archive(automaton.project_name, archive_name, revision=automaton.revision)
    assert content is not None  # same Archive row get_archive_content_type just found this content_type on
    return content.decode("utf-8")
