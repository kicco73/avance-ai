"""Where a project's own files are read from, at run time.

Two places need this and needed it separately until now: a source with a
`url: avance:<path>` (see tracking.sources.avance_archive) and
`attachment.read(name)` (see tracking.actuators.attachment_namespace).
Both ask the same two questions — resolve a name to a stored path, and
give me that file's text and its media type, since both refuse a binary —
and both answered them by going to `Db` at the automaton's own pinned
revision.

That is the one thing a compiled automaton cannot do: it has no storage
location, and no database to have one in. What it has is its own files,
sitting in the package's `data/` directory — so it reads them, and says
where they are through `Automaton.archives_dir`, the one field that
distinguishes an automaton carrying its project from one pointing at a
database.

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

from pathlib import Path
from typing import TYPE_CHECKING

from automaton.media_types import media_type_for
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


class PackageProjectFiles(ProjectFiles):
    """From a directory of real files — a compiled package's own `data/`.
    The only implementation such a product ever composes."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory.resolve()

    def _names(self) -> list[str]:
        return [
            path.relative_to(self._directory).as_posix()
            for path in sorted(self._directory.rglob("*")) if path.is_file()
        ]

    def resolve(self, name: str) -> str | None:
        return self._resolve_among(name, self._names())

    def read(self, path: str) -> tuple[bytes, str] | None:
        candidate = (self._directory / path).resolve()
        # A declared path never escapes the package; a malformed one is
        # simply not found rather than reaching outside it.
        if not candidate.is_file() or self._directory not in candidate.parents:
            return None
        return candidate.read_bytes(), media_type_for(path)


class NoProjectFiles(ProjectFiles):
    """An automaton that neither points at a database nor carries its own
    files — one built in memory by a test. Nothing to find, said plainly
    rather than by raising somewhere further down."""

    def resolve(self, name: str) -> str | None:
        return None

    def read(self, path: str) -> tuple[bytes, str] | None:
        return None


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
    """The one selection point. An automaton that carries its own files
    reads them; one pinned to a stored revision reads those; one with
    neither has nothing to read, whatever database it is handed."""
    if automaton.archives_dir is not None:
        return PackageProjectFiles(automaton.archives_dir)
    if db is None or automaton.revision is None:
        return NoProjectFiles()
    files = DbProjectFiles(db, automaton)
    if session_id is None:
        return files
    return SessionCachedProjectFiles(files, db, automaton, session_id)
