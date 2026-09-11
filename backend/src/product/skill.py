"""The compiled product: this backend serves one packaged project.

The whole package is this file and what it says. There is no service, no
route and no configuration section — being installed *is* the statement,
and what it changes is one thing: where the automaton comes from. A
build that includes `backend/src/product/` reads its project out of the
single compiled package beside it and never opens the Archive tables; a
build that does not, does what it has always done.

A build that also contains src/build/ is not a product, and this stands
down for it: a backend that can compile chooses per project and per
revision whether a package or the interpreted automaton answers, and
that decision is finer than this one. Standing down rather than racing
matters because every build copies src/ whole — this package is in a
backend until somebody unticks it, and a backend that refused to start
because two packages both claimed the loader would be the normal case,
not the exceptional one.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_AUTOMATON_LOADER
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class ProductSkill(Skill):

    key = "product"
    ui_label = "Product"
    ui_description = "Serves one compiled project."

    def start_service(self, raw: dict, path: Path) -> None:
        bus.contribute(POINT_AUTOMATON_LOADER, self._choose_packaged_loader)

    def _choose_packaged_loader(self, choice) -> None:
        from project.archive.packaged_automaton_loader import PackagedAutomatonLoader
        from system import skills

        if any(skill["package"] == "build" for skill in skills.installed()):
            logger.info(
                "build is installed, so this backend can compile and can serve what it compiled — "
                "leaving the automaton loader to it."
            )
            return
        choice.replace(PackagedAutomatonLoader(choice.apps_dir), self.key)
