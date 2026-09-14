from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from db import Db
from events import ProjectRevisionBuildFailed, publish
from system.logging_factory import LoggerFactory

from .layout import ArchiveLayout
from .stored_index_yml import StoredIndexYml

if TYPE_CHECKING:
    from turn.sessions.session_manager import SessionManager

logger = LoggerFactory.get_logger(__name__)


class AutomatonLoader(object):
    def __init__(self, db: Db, session_manager: "SessionManager | None" = None) -> None:
        self._db = db
        self._session_manager = session_manager
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}
        self.__build_failures: dict[tuple[str, int], AutomatonBuildError] = {}

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

        if not AutomatonLoader.is_safe_project_name(project_id):
            raise ValueError(f"Invalid project id: '{project_id}'.")

        archives = self._db.get_archives(project_id, revision=revision)

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
        self.set_cached(project_id, revision, automaton)
        return automaton

    def _repaired(self, project_id: str, revision: int, decoded: dict, refusal):
        try:
            return StoredIndexYml(self._db, project_id, revision).rebuilt(decoded, refusal)
        except AutomatonBuildError as exc:
            exc.project_id = exc.project_id or project_id
            exc.revision = revision
            exc.detail = f"Project '{project_id}', stored revision {revision}: index.yml no longer builds — {exc}"
            self.__build_failures[(project_id, revision)] = exc
            self._handle_broken_revision(project_id, revision, exc)
            raise

    def _handle_broken_revision(self, project_id: str, revision: int, exc: AutomatonBuildError) -> None:
        """Logs once, force-closes any session still open on this exact
        (project_id, revision), and — when `revision` is the project's
        own current published or draft revision, never an older one
        pinned by some session alone — publishes ProjectRevisionBuildFailed
        so ProjectManager can recompute its availability. Only ever
        reached once per (project_id, revision) between invalidations:
        load_at_revision checks/populates the failure cache before calling
        this, so a cache hit never re-runs any of it."""
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

    def load(self, project_id: str) -> Automaton:
        """Whatever's current for `project_id` right now — the most
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses load_at_revision directly."""
        revision = self._db.get_project_revision(project_id)
        return self.load_at_revision(project_id, revision)
