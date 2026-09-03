from __future__ import annotations

from pathlib import Path

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from db import Db

from .layout import ArchiveLayout


class AutomatonLoader:
    def __init__(self, db: Db) -> None:
        self._db = db
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
        """Drops every cached revision of `project_id`, for callers that
        can't tell which revisions are now stale. Ordinary edits go through
        ProjectManager.finalize_update instead, which re-caches just one revision."""
        for key in [k for k in self._automaton_cache if k[0] == project_id]:
            del self._automaton_cache[key]
        for key in [k for k in self._declared_meta_cache if k[0] == project_id]:
            del self._declared_meta_cache[key]

    def set_cached(self, project_id: str, revision: int, automaton: Automaton) -> None:
        self._automaton_cache[(project_id, revision)] = automaton
        self._declared_meta_cache[(project_id, revision)] = (
            automaton.project_id, automaton.family, frozenset(env_key.name for env_key in automaton.env_keys)
        )

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        cache_key = (project_id, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached

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
        except ValueError as exc:
            # Names *which* stored revision no longer builds under the
            # current AutomatonBuilder rules — this surfaces on whichever
            # endpoint happens to touch a session pinned to it, far from
            # any index.yml the caller is looking at.
            raise ValueError(
                f"Project '{project_id}', stored revision {revision}: index.yml no longer builds — {exc}"
            ) from exc
        automaton.set_storage_location(revision)
        self.set_cached(project_id, revision, automaton)
        return automaton

    def load(self, project_id: str) -> Automaton:
        """Whatever's current for `project_id` right now — the most
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses load_at_revision directly."""
        revision = self._db.get_project_revision(project_id)
        return self.load_at_revision(project_id, revision)
