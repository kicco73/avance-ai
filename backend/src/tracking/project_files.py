"""Where a project's own files are read from, at run time.

Three places need this: a source with a `url: avance:<path>` (see
tracking.sources.avance_archive), `attachment.read(name)` (see
tracking.actuators.attachment_namespace), and every turn's own
attachments, which the automaton carries as paths and never as bytes
(see tracking.attachments). All ask the same two questions — resolve a name to a stored path, and
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

Both readers sit behind one process-wide, byte-bounded LRU
(ProjectFileCache): the automaton used to carry every declared file in
memory, so assembling a turn's attachments cost nothing, and reading
them per turn instead only stays free if something remembers them. It is
bounded in bytes rather than in entries because a bound on entries is
not a bound on memory — one 40 MB CSV and one 200-byte note are one
entry each. The key comes from the reader, not from the cache: a stored
project's files are identified by (project id, revision, path) and a
package's by its own data/ path, so neither carries a slot the other
does not use. Entries used to be dropped for free when the automaton
holding them left AutomatonLoader's cache; now they are dropped by the
bound, plus one explicit invalidation where a draft revision is
rewritten in place (ProjectManager.finalize_update).

The per-session cache copy is a decorator rather than part of the
database implementation, because it is a *source's* policy and not a
property of reading from a database: `attachment.read` never cached and
still does not. What it guards against is a draft revision being
rewritten in place while a test session runs on it — not a republish, an
automaton is already per-revision — so it has nothing to do for a
compiled product, which has no draft.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from automaton.media_types import media_type_for
from project.archive.layout import CACHE_DIR

if TYPE_CHECKING:
    from automaton.model import Automaton
    from db import Db

# How DbProjectFiles.cache_key names a file, and so what
# ProjectFileCache.forget_project has to drop. Only the stored-project
# side has anything to invalidate — a package's data/ never changes
# under a running process.
DB_CACHE_KEY_PREFIX = "db:"

# Mirrors AppConfig's own default (config.py, chat-service.
# project-file-cache-bytes) — for a process that never calls
# configure_project_file_cache: a test, a CLI script.
DEFAULT_PROJECT_FILE_CACHE_BYTES = 8 * 1024 * 1024


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

    def cache_key(self, path: str) -> str:
        """What identifies this file across the whole process, for
        ProjectFileCache. Each reader says it in its own terms — there is
        no shared shape, and so no field one of them leaves empty."""
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

    def cache_key(self, path: str) -> str:
        return f"pkg:{self._directory}\0{path}"

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

    def cache_key(self, path: str) -> str:
        raise NotImplementedError("nothing to read, so nothing to cache")


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

    def cache_key(self, path: str) -> str:
        return f"{DB_CACHE_KEY_PREFIX}{self._project_id()}\0{self._revision()}\0{path}"

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


class ProjectFileCache:
    """A byte-bounded LRU of file contents, shared by every reader.

    `max_bytes` bounds the sum of the contents held, not their number: a
    single file larger than the whole bound is served and not kept,
    rather than evicting everything else to make room for something that
    still would not fit."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
        self._bytes = 0

    def resize(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._evict()

    def read(self, key: str, load: "Callable[[], tuple[bytes, str] | None]") -> tuple[bytes, str] | None:
        found = self._entries.get(key)
        if found is not None:
            self._entries.move_to_end(key)
            return found
        loaded = load()
        # A file that isn't there is not cached: it is not a value, and a
        # project gaining the file it was missing must not have to wait
        # for an eviction to be seen.
        if loaded is None:
            return None
        self._store(key, loaded)
        return loaded

    def forget_project(self, project_id: str) -> None:
        """Everything read from `project_id`, at every revision — what a
        save does to a draft revision rewritten in place."""
        prefix = f"{DB_CACHE_KEY_PREFIX}{project_id}\0"
        for key in [k for k in self._entries if k.startswith(prefix)]:
            self._drop(key)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def _store(self, key: str, entry: tuple[bytes, str]) -> None:
        size = len(entry[0])
        if size > self._max_bytes:
            return
        self._entries[key] = entry
        self._bytes += size
        self._evict()

    def _evict(self) -> None:
        while self._bytes > self._max_bytes and self._entries:
            self._drop(next(iter(self._entries)))

    def _drop(self, key: str) -> None:
        self._bytes -= len(self._entries.pop(key)[0])


PROJECT_FILE_CACHE = ProjectFileCache(DEFAULT_PROJECT_FILE_CACHE_BYTES)


def configure_project_file_cache(max_bytes: int) -> None:
    """Called once at boot from the configured value (see main.py)."""
    PROJECT_FILE_CACHE.resize(max_bytes)


class CachedProjectFiles(ProjectFiles):
    """One reader, behind the shared cache. Only `read` goes through it:
    resolution answers a name, not a file, and it is what the byte bound
    is about."""

    def __init__(self, inner: ProjectFiles, cache: ProjectFileCache) -> None:
        self._inner = inner
        self._cache = cache

    def resolve(self, name: str) -> str | None:
        return self._inner.resolve(name)

    def cache_key(self, path: str) -> str:
        return self._inner.cache_key(path)

    def read(self, path: str) -> tuple[bytes, str] | None:
        return self._cache.read(self._inner.cache_key(path), lambda: self._inner.read(path))


def project_files_for(db: "Db | None", automaton: "Automaton", session_id: int | None = None) -> ProjectFiles:
    """The one selection point. An automaton that carries its own files
    reads them; one pinned to a stored revision reads those; one with
    neither has nothing to read, whatever database it is handed. Either
    real reader is composed behind the shared byte-bounded cache, and a
    source's own per-session frozen copy on top of that."""
    if automaton.archives_dir is not None:
        return CachedProjectFiles(PackageProjectFiles(automaton.archives_dir), PROJECT_FILE_CACHE)
    if db is None or automaton.revision is None:
        return NoProjectFiles()
    files = CachedProjectFiles(DbProjectFiles(db, automaton), PROJECT_FILE_CACHE)
    if session_id is None:
        return files
    return SessionCachedProjectFiles(files, db, automaton, session_id)
