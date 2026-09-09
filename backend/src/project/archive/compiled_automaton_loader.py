"""Serves a project's automaton from a compiled package when there is
one, and from Archive rows when there isn't.

A component of the platform, never of the compiled product: it is what
*chooses*, and a built package knows nothing about it.

It is a subclass rather than a parallel implementation because a
compiled automaton is already a drop-in — attribute for attribute, method
for method, the same object an AutomatonBuilder produces, except that it
reads its files from its own data/ directory instead of a database. So
there is exactly one thing to override, `load_at_revision`, and
everything else — the caches, set_cached/invalidate, the cross-project
family scan, the broken-revision handling — is inherited and unchanged.

Only a project's *published* revision is ever served compiled. A draft
changes under the editor's hands and has no build; an older revision some
session is still pinned to had one at most in the past. Both go straight
to the ordinary loader.

Nothing here is fatal. A package that is missing, unimportable, or built
from a different revision degrades to the interpreted automaton with a
line in the log: a product that stops answering because a build went
wrong is worse than one running interpreted.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from build.apps import PackageError, import_automaton, package_dir
from build.build_service import module_name_for
from db import Db
from system.logging_factory import LoggerFactory

from .automaton_loader import AutomatonLoader

if TYPE_CHECKING:
    # Type-only, same reason as AutomatonLoader's own TYPE_CHECKING import.
    from turn.sessions.session_manager import SessionManager

logger = LoggerFactory.get_logger(__name__)


class CompiledAutomatonLoader(AutomatonLoader):

    def __init__(
        self, db: Db, apps_dir: Path, session_manager: "SessionManager | None" = None,
    ) -> None:
        super().__init__(db, session_manager=session_manager)
        self._apps_dir = apps_dir
        # The check-then-import below is not atomic, while the cache dicts
        # it reads and writes are. Held for the whole decision so two
        # requests for the same project cannot both import the package.
        self._compiled_lock = threading.Lock()

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        with self._compiled_lock:
            cached = self._automaton_cache.get((project_id, revision))
            if cached is not None:
                return cached
            compiled = self._compiled_automaton(project_id, revision)
            if compiled is not None:
                return compiled
        return super().load_at_revision(project_id, revision)

    def _compiled_automaton(self, project_id: str, revision: int) -> Automaton | None:
        """The compiled package for this exact (project, revision), or
        None for every reason there might not be one — which is a normal
        answer, not a failure."""
        if revision != self._db.get_project_published_revision(project_id):
            return None
        directory = package_dir(self._apps_dir, module_name_for(project_id), revision)
        if not directory.is_dir():
            return None
        try:
            automaton = import_automaton(directory, project_id, revision)
        except PackageError as exc:
            logger.error("Compiled package unusable, falling back to the interpreted automaton: %s", exc)
            return None
        # The one thing a compiled automaton does not know about itself:
        # it is built with revision None, and whoever loads it says which
        # stored revision it stands for — exactly as AutomatonLoader does
        # for an interpreted one. Its package directory is per-revision,
        # so this is stamped once and never changes under anyone.
        automaton.set_storage_location(revision)
        self.set_cached(project_id, revision, automaton)
        logger.info("Serving project '%s' revision %s from %s.", project_id, revision, directory)
        return automaton
