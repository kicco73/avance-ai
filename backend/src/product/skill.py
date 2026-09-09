"""The compiled product: this backend serves one packaged project.

The whole package is this file and what it says. There is no service, no
route and no configuration section — being installed *is* the statement,
and what it changes is one thing: where the automaton comes from. A
build that includes `backend/src/product/` reads its project out of the
single compiled package beside it and never opens the Archive tables; a
build that does not, does what it has always done.

It is deliberately incompatible with the authoring platform. Both claim
the automaton loader, and AutomatonLoaderChoice.replace refuses the
second claim by name rather than letting the last one win — an editor
and a compiled product in the same backend is a build that should not
have been made, and the error says which two packages made it.
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

    choice.replace(PackagedAutomatonLoader(choice.apps_dir), KEY)


def stop() -> None:
    """Nothing to release: the package is imported once and freed with
    the process."""
