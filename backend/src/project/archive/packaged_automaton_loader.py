"""The loader a compiled product uses: one project, one revision, one
package, decided once at boot.

AutomatonLoader answers "which automaton for this project at this
revision", and CompiledAutomatonLoader adds "compiled or interpreted?"
on top of it. A product has neither question. It ships one package, it
cannot be edited, there is no draft, no second revision, no other
project, and no Archive rows to fall back to. So this is not a subclass
of either: it is the same two methods with all the choosing removed.

Three differences from the platform's loaders, and each is the point:

  - it never reads the database. What a package needs is inside the
    package (see the compiler's own data/ directory).
  - it fails at construction, not per load. On the platform a package
    that will not import degrades to the interpreted automaton with a
    line in the log, because a project running interpreted beats a
    project not running. Here there is nothing to degrade to, so a bad
    package is a backend that does not start — which is the honest
    outcome and the one that gets noticed.
  - it does not cache, because there is nothing to invalidate. The
    automaton is imported once and the same object is handed out for the
    life of the process.

`invalidate`, `invalidate_cache` and `set_cached` exist and do nothing,
so a caller that speaks to a loader does not have to know which one it
got. Everything else on AutomatonLoader — the family scan, env keys
across projects, build-failure bookkeeping, closing sessions pinned to a
revision that stopped building — is asked for only by the authoring
surface, and is absent here rather than raising.
"""
from __future__ import annotations

from pathlib import Path

from automaton.automaton import Automaton
from system.logging_factory import LoggerFactory

from .packages import PackageError, declared_revision, import_automaton, sole_package

logger = LoggerFactory.get_logger(__name__)


class PackagedAutomatonLoader(object):

    def __init__(self, apps_dir: Path) -> None:
        directory = sole_package(apps_dir)
        self._revision = declared_revision(directory)
        # The package declares which project it is; nothing outside it
        # gets to disagree, which is why import_automaton is given the
        # package's own name rather than a configured one.
        self._automaton: Automaton = import_automaton(directory, directory.name, self._revision)
        self._project_id = self._automaton.project_id
        logger.info(
            "Serving '%s' revision %s from %s — one project, compiled.",
            self._project_id, self._revision, directory,
        )

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def revision(self) -> int:
        return self._revision

    def load(self, project_id: str) -> Automaton:
        return self.load_at_revision(project_id, self._revision)

    def load_at_revision(self, project_id: str, revision: int) -> Automaton:
        """The one automaton. A request for a different project or a
        different revision is not a miss to be resolved — there is
        nothing else here — so it raises rather than quietly serving
        what it has."""
        if project_id != self._project_id:
            raise PackageError(f"This build serves '{self._project_id}', not '{project_id}'.")
        if revision != self._revision:
            raise PackageError(
                f"This build serves '{self._project_id}' at revision {self._revision}, not {revision}."
            )
        return self._automaton

    def invalidate(self, project_id: str, revision: int) -> None:
        """Nothing to invalidate: the package cannot change under a
        running process, and there is no editor to change it."""

    def invalidate_cache(self, project_id: str | None = None) -> None:
        """See invalidate."""

    def set_cached(self, project_id: str, revision: int, automaton: Automaton) -> None:
        """See invalidate. A caller offering a freshly built automaton
        has one this loader did not produce, and it is not wanted."""
