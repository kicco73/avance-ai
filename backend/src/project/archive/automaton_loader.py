from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from db import Db
from events import ProjectRevisionBuildFailed, publish
from logging_factory import LoggerFactory

from .layout import ArchiveLayout

if TYPE_CHECKING:
    # Type-only: ChatSessionManager doesn't import this module, so a real
    # top-level import would be safe too, but every other cross-package
    # dependency here already sits behind TYPE_CHECKING/local imports —
    # kept consistent rather than the one exception.
    from chat.session_manager import ChatSessionManager

logger = LoggerFactory.get_logger(__name__)


class AutomatonLoader:
    def __init__(self, db: Db, session_manager: "ChatSessionManager | None" = None) -> None:
        self._db = db
        # Only for force-closing a session still open on a stored revision
        # that no longer builds (see load_at_revision) — None is fine for
        # any caller with no session to worry about, it just means that
        # cleanup never runs.
        self._session_manager = session_manager
        # (project_id, revision) -> Automaton. Revision-keyed so a caller
        # pinned to one specific revision and a caller wanting "whatever's
        # current" can share the cache without cross-serving.
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}
        # Same (project_id, revision) keying as _automaton_cache above, and
        # for the same reason: a session pinned to an old revision can
        # populate this via set_cached too (through load_at_revision), and
        # that stale revision's family/env-keys must never answer for
        # "whatever's current" — the only thing known_projects_env_keys
        # scans other projects for. (declared_id, family, env_key_names).
        self._declared_meta_cache: dict[tuple[str, int], tuple[str | None, str | None, frozenset[str]]] = {}
        # (project_id, revision) -> the AutomatonBuildError it last raised.
        # A revision that doesn't build is just as cacheable as one that
        # does: load_at_revision consults this first and re-raises without
        # rebuilding, and without re-running _handle_broken_revision's own
        # log/close-sessions/event side effects — those fire exactly once
        # per (project_id, revision), the first time it's discovered
        # broken, until something actually invalidates this entry (see
        # invalidate/invalidate_cache below). Doubles as what
        # _broken_revisions used to be for (never re-run the close sweep
        # for the same one twice) — a key present here already means that ran.
        self._build_failures: dict[tuple[str, int], AutomatonBuildError] = {}

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

    def known_projects_env_keys(self, project_id: str, family: str | None) -> dict[str, frozenset[str]]:
        """Every *other* project's declared project.id mapped to its
        declared env key names, for AutomatonBuilder.build's automaton.*
        existence check — narrowed to projects declaring this exact same
        `family` (never parsed, plain string equality): a project outside
        it (or `family` itself being None) is invisible here, so an
        out-of-family — or family-less — automaton.<id> reference fails
        build validation exactly like referencing an id that doesn't
        exist at all. `family` is `project_id`'s own declared family —
        the caller peeks it (AutomatonBuilder.read_declared_env_keys) off
        whatever index.yml it's about to build, since that project's own
        Automaton doesn't exist yet at this point."""
        if family is None:
            return {}
        known: dict[str, frozenset[str]] = {}
        for other_id in self._db.list_projects():
            if other_id == project_id:
                continue
            other_declared_id, other_family, env_keys = self._declared_meta(other_id)
            if other_declared_id is not None and other_family == family:
                known[other_declared_id] = env_keys
        return known

    def _declared_meta(self, project_id: str) -> tuple[str | None, str | None, frozenset[str]]:
        """(declared_id, family, env_key_names) off `project_id`'s *current*
        index.yml — cached per (project_id, current revision) so a family
        scan across every project (see known_projects_env_keys) parses each
        sibling's YAML at most once per revision. Resolving the current
        revision is one cheap int lookup, still far short of the archive
        fetch + full YAML parse it replaces on a cache hit."""
        revision = self._db.get_project_revision(project_id)
        cache_key = (project_id, revision)
        cached = self._declared_meta_cache.get(cache_key)
        if cached is not None:
            return cached
        archive = self._db.get_archive(project_id, "index.yml", revision=revision)
        if archive is None:
            return None, None, frozenset()
        meta = AutomatonBuilder.read_declared_env_keys(archive.decode("utf-8"))
        self._declared_meta_cache[cache_key] = meta
        return meta

    def declared_family(self, project_id: str) -> str | None:
        """`project_id`'s current declared project.family — cached, for
        scanning every other project by family (e.g.
        ProjectInspector.get_identifier_registry) without a full build."""
        return self._declared_meta(project_id)[1]

    def invalidate_cache(self, project_id: str) -> None:
        """Drops every cached revision of `project_id` — both what it
        last built successfully and what it last failed to build — for
        callers that can't tell which revisions are now stale (a rename,
        a publish that re-stamps the draft's own project.revision, a
        revert). Ordinary edits go through ProjectManager.finalize_update
        instead, which re-caches just one revision (see set_cached)."""
        for key in [k for k in self._automaton_cache if k[0] == project_id]:
            del self._automaton_cache[key]
        for key in [k for k in self._declared_meta_cache if k[0] == project_id]:
            del self._declared_meta_cache[key]
        for key in [k for k in self._build_failures if k[0] == project_id]:
            del self._build_failures[key]

    def invalidate(self, project_id: str, revision: int) -> None:
        """Same as invalidate_cache, narrowed to one exact revision —
        every write to that revision's own stored files (a design-view
        save, a legacy migration rewriting index.yml in place, an
        upload/import, a publish/revert) must call this, or a stale
        success *or* a stale failure could otherwise outlive the content
        it was cached for."""
        cache_key = (project_id, revision)
        self._automaton_cache.pop(cache_key, None)
        self._declared_meta_cache.pop(cache_key, None)
        self._build_failures.pop(cache_key, None)

    def set_cached(self, project_id: str, revision: int, automaton: Automaton) -> None:
        self._automaton_cache[(project_id, revision)] = automaton
        self._declared_meta_cache[(project_id, revision)] = (
            automaton.project_id, automaton.family, frozenset(env_key.name for env_key in automaton.env_keys)
        )
        # A fresh success supersedes any stale failure cached for this
        # exact key (e.g. a save that fixes what a previous one broke).
        self._build_failures.pop((project_id, revision), None)

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        cache_key = (project_id, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached
        cached_failure = self._build_failures.get(cache_key)
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
        _, family, _ = AutomatonBuilder.read_declared_env_keys(decoded['index.yml'])
        try:
            # legacy_project_id: a revision stored before `project.id`
            # became mandatory (see AutomatonBuilder._build_project_metadata)
            # still has sessions pinned to it — its identity is this row's.
            automaton = AutomatonBuilder().build(
                decoded, self.known_projects_env_keys(project_id, family), legacy_project_id=project_id,
            )
        except AutomatonBuildError as exc:
            # Names *which* stored revision no longer builds under the
            # current AutomatonBuilder rules — this surfaces on whichever
            # endpoint happens to touch a session pinned to it, far from
            # any index.yml the caller is looking at. Kept in `detail`,
            # not `message` — the builder's own message (and its line/
            # section) stay exactly as it raised them, for a caller that
            # cares about the structured fields rather than this summary.
            exc.project_id = exc.project_id or project_id
            exc.revision = revision
            exc.detail = f"Project '{project_id}', stored revision {revision}: index.yml no longer builds — {exc}"
            self._build_failures[cache_key] = exc
            self._handle_broken_revision(project_id, revision, exc)
            raise
        automaton.set_storage_location(revision)
        self.set_cached(project_id, revision, automaton)
        return automaton

    def _handle_broken_revision(self, project_id: str, revision: int, exc: AutomatonBuildError) -> None:
        """Logs once, force-closes any session still open on this exact
        (project_id, revision), and — when `revision` is the project's
        own current published or draft revision, never an older one
        pinned by some session alone — publishes ProjectRevisionBuildFailed
        so ProjectManager can recompute its availability. Only ever
        reached once per (project_id, revision) between invalidations:
        load_at_revision checks/populates _build_failures before calling
        this, so a cache hit never re-runs any of it (this used to be
        its own separate _broken_revisions dedup set; the failure cache
        now serves that purpose too)."""
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
