"""Where a project's own files are read from, at run time.

Two places need this and needed it separately until now: a source with a
`url: avance:<path>` (see tracking.sources.avance_archive) and
`attachment.read(name)` (see tracking.actuators.attachment_namespace).
Both ask the same two questions — resolve a name to a stored path, and
give me that file's text and its media type, since both refuse a binary —
and both answered them by going to `Db` at the automaton's own pinned
revision.

That is the one thing a compiled automaton cannot do: it has no storage
location, and no database to have one in. It does, however, already carry
every file of its project in memory, converted once, each with its own
media type (`AutomatonBuilder` hands `attachments=all_archives` to every
Automaton alike, and a generated module hands it exactly the same). So
reading from a compiled package is a lookup in what the automaton is
already holding, not a filesystem access and not a new attribute.

Which implementation a caller gets is decided once, where it is
constructed (SourceNamespace for sources, EvaluationScopeBuilder for the
attachment namespace) — never re-checked inside a driver.

The per-session cache copy is a decorator rather than part of the
database implementation, because it is a *source's* policy and not a
property of reading from a database: `attachment.read` never cached and
still does not. What it guards against is a draft revision being
rewritten in place while a test session runs on it — not a republish, an
automaton is already per-revision — so it has nothing to do for a
compiled product, which has no draft.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from project.archive.layout import CACHE_DIR

if TYPE_CHECKING:
    from automaton.model import Automaton
    from db import Db


class ProjectFiles:
    """`resolve` turns a declared name into a stored path — an exact
    match, or a unique basename, the same resolution a project's own
    `attachments:` list uses. `read` returns (raw bytes, media_type), or
    None if there is no such file. Bytes, not text, deliberately: both
    callers refuse a binary file with a message of their own, and they
    can only do that if the media type reaches them before anything has
    tried to decode."""

    def resolve(self, name: str) -> str | None:
        raise NotImplementedError

    def read(self, path: str) -> tuple[bytes, str] | None:
        raise NotImplementedError

    @staticmethod
    def _resolve_among(name: str, names: list[str]) -> str | None:
        if name in names:
            return name
        matches = [candidate for candidate in names if Path(candidate).name == name]
        return matches[0] if len(matches) == 1 else None


class AutomatonProjectFiles(ProjectFiles):
    """From what the automaton itself carries. The only implementation a
    compiled product ever composes, and the one anything without a
    storage location falls back to."""

    def __init__(self, automaton: "Automaton") -> None:
        self._attachments = automaton.attachments

    def resolve(self, name: str) -> str | None:
        return self._resolve_among(name, list(self._attachments))

    def read(self, path: str) -> tuple[bytes, str] | None:
        archive = self._attachments.get(path)
        if archive is None:
            return None
        # Text came in as text and binary as base64 (see
        # ArchiveResolver.convert_contents_to_archives) — undone here so
        # every implementation hands back the same thing.
        data, media_type = archive.source["data"], archive.source["media_type"]
        raw = data.encode("utf-8") if archive.source["type"] == "text" else base64.b64decode(data)
        return raw, media_type


class DbProjectFiles(ProjectFiles):
    """From the Archive rows of this automaton's own pinned revision —
    never Db's own "current" default, wrong for a session pinned to an
    older one."""

    def __init__(self, db: "Db", automaton: "Automaton") -> None:
        self._db = db
        self._automaton = automaton

    def resolve(self, name: str) -> str | None:
        archives = self._db.get_archives(self._project_id(), revision=self._revision())
        return self._resolve_among(name, list(archives))

    def read(self, path: str) -> tuple[bytes, str] | None:
        media_type = self._db.get_archive_content_type(self._project_id(), path, revision=self._revision())
        if media_type is None:
            return None
        content = self._db.get_archive(self._project_id(), path, revision=self._revision())
        assert content is not None  # the same Archive row get_archive_content_type just found
        return content, media_type

    def _project_id(self) -> str:
        assert self._automaton.project_id is not None
        return self._automaton.project_id

    def _revision(self) -> int:
        assert self._automaton.revision is not None
        return self._automaton.revision


class SessionCachedProjectFiles(ProjectFiles):
    """One session's own frozen copy of whatever it reads, duplicated
    from `inner` on a miss. The first read of a session pays for a second
    round trip; in exchange the rest of the conversation keeps seeing the
    same content even if the project is edited underneath it."""

    def __init__(self, inner: ProjectFiles, db: "Db", automaton: "Automaton", session_id: int) -> None:
        self._inner = inner
        self._db = db
        self._automaton = automaton
        self._session_id = session_id

    def resolve(self, name: str) -> str | None:
        return self._inner.resolve(name)

    def read(self, path: str) -> tuple[bytes, str] | None:
        project_id, revision = self._automaton.project_id, self._automaton.revision
        assert project_id is not None and revision is not None
        cache_path = f"{CACHE_DIR}/sessions/{self._session_id}/{path}"
        cached = self._db.get_archive(project_id, cache_path, revision=revision)
        if cached is not None:
            media_type = self._db.get_archive_content_type(project_id, cache_path, revision=revision)
            return cached, media_type or "text/plain"
        found = self._inner.read(path)
        if found is None:
            return None
        content, media_type = found
        self._db.write_archive_at_revision(project_id, cache_path, revision, content, media_type)
        return content, media_type


def project_files_for(db: "Db | None", automaton: "Automaton", session_id: int | None = None) -> ProjectFiles:
    """The one selection point. An automaton with no storage location —
    a compiled one, or one built in memory — has nothing to read from a
    database, whatever database it is handed."""
    if db is None or automaton.revision is None:
        return AutomatonProjectFiles(automaton)
    files = DbProjectFiles(db, automaton)
    if session_id is None:
        return files
    return SessionCachedProjectFiles(files, db, automaton, session_id)
