"""Which loader the engine will use, offered once and settled once.

The core has one loader it can always build — the Db/Archive-backed
AutomatonLoader — and no opinion about the others, because the others
live in packages a build may not contain. So main.py builds the default,
publishes this on bus.POINT_AUTOMATON_LOADER, and uses whatever `settled`
returns.

`replace` refuses a second replacement rather than letting the last
contributor win by accident. Two packages both claiming the loader is a
build that should not have been made, and a message naming both is worth
more than a silent choice nobody can trace afterwards.

Serving a single compiled package is **not** one of those claims, and
used to be: it was a package of its own whose entire content was this
decision, which a build then had to be told to include — while the
loader it chose lives in the core and travels in every delivery anyway.
So excluding it removed no behaviour, only the ability to reach behaviour
that shipped regardless, and the default left it out of precisely the
deliveries that cannot run without it.

It is derived here instead, from two facts this object already holds.
Nobody claiming the loader means no compiler and no authoring surface; a
database with no project means nothing of its own to serve. A backend
that is both, with one package beside it, is a product — there is
nothing else it could be, and nothing to tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from system.logging_factory import LoggerFactory

from .packages import PackageError

logger = LoggerFactory.get_logger(__name__)


@dataclass
class AutomatonLoaderChoice:
    """`db`, `session_manager` and `apps_dir` are what a replacement is
    likely to need to build itself; `loader` is what the engine will
    actually get."""

    db: Any
    session_manager: Any
    apps_dir: Path
    loader: Any
    chosen_by: str = "core"

    def replace(self, loader: Any, by: str) -> None:
        if self.chosen_by != "core":
            raise RuntimeError(
                f"Both '{self.chosen_by}' and '{by}' claim the automaton loader — "
                f"this backend was built with two packages that cannot coexist."
            )
        self.loader = loader
        self.chosen_by = by

    def settled(self) -> Any:
        """What the engine gets. Whoever claimed it wins; otherwise a
        sole package beside a database with no project of its own is a
        compiled product, and reading that package is the only way such a
        backend answers at all."""
        if self.chosen_by != "core":
            return self.loader
        for packaged in filter(None, [self._sole_package()]):
            self.chosen_by = "package"
            return packaged
        return self.loader

    def _sole_package(self) -> Any:
        from .packaged_automaton_loader import PackagedAutomatonLoader

        if self.db.list_projects():
            return None
        try:
            return PackagedAutomatonLoader(self.apps_dir)
        except PackageError as exc:
            logger.info(
                "Nothing of this backend's own to serve and no package to read, "
                "so the database stays the source: %s", exc,
            )
            return None
