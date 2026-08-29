from __future__ import annotations

from pathlib import Path

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from db import Db

from .layout import ArchiveLayout


class AutomatonLoader:
    def __init__(self, db: Db) -> None:
        self._db = db
        # (project_name, revision) -> Automaton. Revision-keyed so a caller
        # pinned to one specific revision and a caller wanting "whatever's
        # current" can share the cache without cross-serving.
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}

    @staticmethod
    def is_safe_project_name(project_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not project_name or project_name in (".", ".."):
            return False
        return Path(project_name).name == project_name

    def known_projects_env_keys(self, project_name: str) -> dict[str, frozenset[str]]:
        """Every *other* project's declared project.id mapped to its
        declared env key names, for AutomatonBuilder.build's automaton.*
        existence check."""
        known: dict[str, frozenset[str]] = {}
        for other_name in self._db.list_projects():
            if other_name == project_name:
                continue
            archive = self._db.get_archive(other_name, "index.yml")
            if archive is None:
                continue
            project_id, env_keys = AutomatonBuilder.read_declared_env_keys(archive.decode("utf-8"))
            if project_id is not None:
                known[project_id] = env_keys
        return known

    def invalidate_cache(self, project_name: str) -> None:
        """Drops every cached revision of `project_name`, for callers that
        can't tell which revisions are now stale. Ordinary edits go through
        ProjectManager.finalize_update instead, which re-caches just one revision."""
        for key in [k for k in self._automaton_cache if k[0] == project_name]:
            del self._automaton_cache[key]

    def set_cached(self, project_name: str, revision: int, automaton: Automaton) -> None:
        self._automaton_cache[(project_name, revision)] = automaton

    def load_at_revision(self, project_name: str, revision: int) -> Automaton:
        cache_key = (project_name, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached

        if not AutomatonLoader.is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        archives = self._db.get_archives(project_name, revision=revision)

        if not archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not exist.")
        if 'index.yml' not in archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not contain 'index.yml'.")

        automaton = AutomatonBuilder().build(
            ArchiveLayout.decode_text(archives), self.known_projects_env_keys(project_name)
        )
        self._automaton_cache[cache_key] = automaton
        return automaton

    def load(self, project_name: str) -> Automaton:
        """Whatever's current for `project_name` right now — the most
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses load_at_revision directly."""
        revision = self._db.get_project_revision(project_name)
        return self.load_at_revision(project_name, revision)
