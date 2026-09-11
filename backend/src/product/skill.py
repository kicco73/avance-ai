"""The compiled product: this backend serves one packaged project.

The whole package is this file and what it says. There is no service, no
route and no configuration section — being installed *is* the statement,
and what it changes is one thing: where the automaton comes from. A
build that includes `backend/src/product/` reads its project out of the
single compiled package beside it and never opens the Archive tables; a
build that does not, does what it has always done.

A build that also contains the authoring platform is not a product, and
this stands down for it: the editor writes revisions, so the loader that
reads them has to win. Standing down rather than racing matters because
every build copies src/ whole — this package is in a backend until
somebody unticks it, and a backend that refuses to start because two
packages both claimed the loader would be the normal case, not the
exceptional one.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_AUTOMATON_LOADER
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

KEY = "product"
LABEL = "Compiled product — serve one packaged project"


def start(raw: dict, path: Path) -> None:
    bus.contribute(POINT_AUTOMATON_LOADER, _choose_packaged_loader)


def _choose_packaged_loader(choice) -> None:
    from project.archive.packaged_automaton_loader import PackagedAutomatonLoader
    from system import skills

    if any(skill["package"] == "avance_platform" for skill in skills.installed()):
        logger.info(
            "avance_platform is installed, so this backend is a platform and not a product — "
            "leaving the automaton loader to it."
        )
        return
    choice.replace(PackagedAutomatonLoader(choice.apps_dir), KEY)


def stop() -> None:
    """Nothing to release: the package is imported once and freed with
    the process."""
