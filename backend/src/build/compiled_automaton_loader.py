"""Serves a project's automaton from a compiled package when there is
one, and from Archive rows when there isn't.

A component of the platform, never of the compiled product: it is what
*chooses*, and a built package knows nothing about it.

It is a subclass rather than a parallel implementation because a
compiled automaton is already a drop-in — attribute for attribute, method
for method, the same object an AutomatonBuilder produces, except that it
reads its files from its own data/ directory instead of a database. So
there are only two things to override — `load_at_revision` and `load` —
and everything else — the caches, set_cached/invalidate, the cross-project
family scan, the broken-revision handling — is inherited and unchanged.

Only a project's *published* revision is ever served compiled. A draft
changes under the editor's hands and has no build; an older revision some
session is still pinned to had one at most in the past. Both go straight
to the ordinary loader — `load`'s own override forces this even when the
draft's revision number happens to equal the published one (true right
after a publish, until the next edit forks it — see
project/archive/automaton_loader.py's own BasicAutomatonLoader docstring),
since `load_at_revision` alone can't tell those two callers apart. It
does this by handing the draft lookup to `_draft_loader`, a plain
BasicAutomatonLoader — not this class, not even a second AutomatonLoader
— so it shares no cache with `load_at_revision`'s own compiled decision
in either direction: nothing a live request warms is ever read by
`load`, and nothing `load` builds is ever cached anywhere to go stale or
need evicting.

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
from project.archive.packages import PackageError, import_automaton, package_dir
from build.build_service import module_name_for
from db import Db
from system.logging_factory import LoggerFactory

from project.archive.automaton_loader import AutomatonLoader, BasicAutomatonLoader

if TYPE_CHECKING:
    from turn.sessions.session_manager import SessionManager

logger = LoggerFactory.get_logger(__name__)


class CompiledAutomatonLoader(AutomatonLoader):

    def __init__(
        self, db: Db, apps_dir: Path, session_manager: "SessionManager | None" = None,
    ) -> None:
        super().__init__(db, session_manager=session_manager)
        self._apps_dir = apps_dir
        self._compiled_lock = threading.Lock()
        self._draft_loader = BasicAutomatonLoader(db, session_manager=session_manager)

    def load(self, project_id: str) -> Automaton:
        return self._draft_loader.load(project_id)

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
        automaton.set_storage_location(revision)
        self.set_cached(project_id, revision, automaton)
        logger.info("Serving project '%s' revision %s from %s.", project_id, revision, directory)
        return automaton
