"""Compiling a project, as something the platform finds rather than
builds into itself.

Two things are registered from here, and they are the two halves of one
capability: the routes that produce a package, and the loader that
prefers one over the interpreted automaton when it exists. Neither
belongs to the authoring surface — an editor that cannot compile is a
perfectly good editor, and a product that can compile is a contradiction.

A build without `backend/src/build/` has no /api/build routes, no Build
view to answer them, and reads every project from its Archive rows. It
is also the shape a compiled product wants: nothing here is of any use
to a backend that serves a package somebody else built.

Registered from `_install`, not from `start`: start() runs at boot,
before the services BuildController needs exist; `_install` runs when
AvanceController assembles its router, which is after (see
bus.POINT_CORE_SERVICES).
"""
from __future__ import annotations

from pathlib import Path

from build import config as build_config
from system import bus
from system.bus import POINT_AUTOMATON_LOADER, POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

KEY = "build"
LABEL = "Build — compile a project into a package"


def start(raw: dict, path: Path) -> None:
    bus.contribute(POINT_HTTP_CONTROLLERS, _install)
    if build_config.serves_compiled(raw, path):
        bus.contribute(POINT_AUTOMATON_LOADER, _choose_compiled_loader)


def _choose_compiled_loader(choice) -> None:
    """Compiled when there is a package for the project's published
    revision, interpreted otherwise. The choosing is this service's: it
    is the only one that knows a package can exist at all (see
    build/compiled_automaton_loader.py)."""
    from build.compiled_automaton_loader import CompiledAutomatonLoader

    choice.replace(
        CompiledAutomatonLoader(choice.db, choice.apps_dir, session_manager=choice.session_manager),
        KEY,
    )


def _install(controllers: list) -> None:
    # XXX Compiled automaton requirement - do not touch.
    # XXX The Build view's Target step, wired to a real compile. The
    # directory it writes into is the one CompiledAutomatonLoader reads
    # from — one setting (build-service.apps-dir), never two.
    from build import BuildService
    from build.build_controller import BuildController

    core = bus.collect(POINT_CORE_SERVICES, {})
    controllers.append(
        BuildController(BuildService(core["db"], core["project_service"], core["apps_dir"]))
    )
    logger.info("build started — a project can be compiled into a package.")


def stop() -> None:
    """Nothing to release: a build runs to completion inside its own
    request, and the packages it wrote outlive the process."""
