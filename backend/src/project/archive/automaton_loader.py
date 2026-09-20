from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from automaton.file_types import ProjectFileTypes
from db import Db
from events import ProjectRevisionBuildFailed, publish
from system.logging_factory import LoggerFactory

from .layout import ArchiveLayout
from .stored_index_yml import StoredIndexYml

if TYPE_CHECKING:
    from turn.sessions.session_manager import SessionManager

logger = LoggerFactory.get_logger(__name__)


class BasicAutomatonLoader(object):
    """Builds one Automaton from a project's Archive rows — nothing
    cached, nothing remembered between calls. Two calls for the same
    (project_id, revision) do two separate builds; that is the whole
    point of using this rather than AutomatonLoader, which adds a cache
    on top of exactly this. A caller that must always see the current
    content of a revision that keeps being rewritten in place uses this
    directly, precisely to have nothing to keep in sync with anyone
    else's cache."""

    def __init__(self, db: Db, session_manager: "SessionManager | None" = None) -> None:
        self._db = db
        self._session_manager = session_manager

    @staticmethod
    def is_safe_project_name(project_id: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename. A defensive check on raw/untrusted
        input (e.g. a URL path param) ahead of any DB lookup — every
        project.id that actually passed AutomatonBuilder's own stricter
        dot-segmented grammar already satisfies this trivially."""
        if not project_id or project_id in (".", ".."):
            return False
        return Path(project_id).name == project_id

    def load(self, project_id: str) -> Automaton:
        """Whatever's current for `project_id` right now — the most
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses load_at_revision directly."""
        revision = self._db.get_project_revision(project_id)
        return self.load_at_revision(project_id, revision)

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        if not self.is_safe_project_name(project_id):
            raise ValueError(f"Invalid project id: '{project_id}'.")

        archives = self._text_archives(project_id, revision)

        if not archives:
            raise  FileNotFoundError(f"Project '{project_id}' does not exist.")
        if 'index.yml' not in archives:
            raise  FileNotFoundError(f"Project '{project_id}' does not contain 'index.yml'.")

        decoded = ArchiveLayout.decode_text(archives)
        try:
            automaton = AutomatonBuilder().build(decoded, legacy_project_id=project_id)
        except AutomatonBuildError as refusal:
            automaton = self._repaired(project_id, revision, decoded, refusal)
        automaton.set_storage_location(revision)
        return automaton

    def _text_archives(self, project_id: str, revision: int) -> dict[str, bytes]:
        names = list(self._db.get_archive_hashes(project_id, revision=revision))
        contents = self._db.get_archive_contents(
            project_id, revision, [name for name in names if ProjectFileTypes.of(name).text],
        )
        return {name: contents.get(name, b"") for name in names}

    def _repaired(self, project_id: str, revision: int, decoded: dict, refusal):
        try:
            return StoredIndexYml(self._db, project_id, revision).rebuilt(decoded, refusal)
        except AutomatonBuildError as exc:
            exc.project_id = exc.project_id or project_id
            exc.revision = revision
            exc.detail = f"Project '{project_id}', stored revision {revision}: index.yml no longer builds — {exc}"
            self._handle_broken_revision(project_id, revision, exc)
            raise

    def _handle_broken_revision(self, project_id: str, revision: int, exc: AutomatonBuildError) -> None:
        """Logs, force-closes any session still open on this exact
        (project_id, revision), and — when `revision` is the project's
        own current published or draft revision, never an older one
        pinned by some session alone — publishes ProjectRevisionBuildFailed
        so ProjectManager can recompute its availability. AutomatonLoader's
        own failure cache makes this run once per (project_id, revision)
        between invalidations; here, with no cache to gate it, it runs on
        every failed call — the broken-build path only, never the hot one."""
        logger.warning(
            "Project '%s', stored revision %s no longer builds — %s", project_id, revision, exc,
        )
        if self._session_manager is not None:
            for session in self._db.list_live_sessions_for_revision(project_id, revision):
                if self._session_manager.is_open(session):
                    self._session_manager.close_session(session, 'revision-invalid')
        if not self._db.project_exists(project_id):
            return
        is_current_or_published = (
            revision == self._db.get_project_revision(project_id)
            or revision == self._db.get_project_published_revision(project_id)
        )
        if is_current_or_published:
            publish(ProjectRevisionBuildFailed(project_id=project_id, revision=revision))


class AutomatonLoader(BasicAutomatonLoader):
    """BasicAutomatonLoader plus a cache: a revision's own build is
    stable once known (an edit rewrites the row and calls set_cached
    with the fresh result — see ProjectManager.finalize_update — rather
    than relying on load_at_revision noticing the row changed), so
    everything served through the ordinary platform paths keeps that
    build in memory instead of re-parsing the same YAML on every turn."""

    def __init__(self, db: Db, session_manager: "SessionManager | None" = None) -> None:
        super().__init__(db, session_manager=session_manager)
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}
        self.__build_failures: dict[tuple[str, int], AutomatonBuildError] = {}

    def invalidate_cache(self, project_id: str) -> None:
        """Drops every cached revision of `project_id` — both what it
        last built successfully and what it last failed to build — for
        callers that can't tell which revisions are now stale (a rename,
        a publish that re-stamps the draft's own project.revision, a
        revert). Ordinary edits go through ProjectManager.finalize_update
        instead, which re-caches just one revision (see set_cached)."""
        for key in [k for k in self._automaton_cache if k[0] == project_id]:
            del self._automaton_cache[key]
        for key in [k for k in self.__build_failures if k[0] == project_id]:
            del self.__build_failures[key]

    def invalidate(self, project_id: str, revision: int) -> None:
        """Same as invalidate_cache, narrowed to one exact revision —
        every write to that revision's own stored files (a design-view
        save, a legacy migration rewriting index.yml in place, an
        upload/import, a publish/revert) must call this, or a stale
        success *or* a stale failure could otherwise outlive the content
        it was cached for."""
        cache_key = (project_id, revision)
        self._automaton_cache.pop(cache_key, None)
        self.__build_failures.pop(cache_key, None)

    def set_cached(self, project_id: str, revision: int, automaton: Automaton) -> None:
        self._automaton_cache[(project_id, revision)] = automaton
        self.__build_failures.pop((project_id, revision), None)

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        cache_key = (project_id, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached
        cached_failure = self.__build_failures.get(cache_key)
        if cached_failure is not None:
            raise cached_failure

        try:
            automaton = super().load_at_revision(project_id, revision)
        except AutomatonBuildError as exc:
            self.__build_failures[cache_key] = exc
            raise
        self.set_cached(project_id, revision, automaton)
        return automaton
