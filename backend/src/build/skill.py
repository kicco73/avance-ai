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

Its controller arrives from `register_controllers`, not from
`start_service`: starting runs at boot, before the services
BuildController needs exist; the router is assembled after (see
bus.POINT_CORE_SERVICES).
"""
from __future__ import annotations

from pathlib import Path

from build import config as build_config
from system import bus
from system.bus import (
    POINT_API_STATE, POINT_AUTOMATON_LOADER, POINT_CORE_SERVICES,
)
from system.wiring import construct
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class BuildSkill(Skill):

    key = "build"
    ui_label = "Build"
    ui_description = "Compiles a project into a standalone package."

    def start_service(self, raw: dict, path: Path) -> None:
        # The one thing the frontend cannot find out by itself: whether this
        # backend can compile at all. Manage projects' Publish button compiles
        # right after publishing where it can, and simply publishes where it
        # cannot — a backend without this package never sends the field, which
        # is the answer.
        bus.contribute(POINT_API_STATE, lambda payload: payload.update({"build_enabled": True}))
        if build_config.serves_compiled(raw, path):
            bus.contribute(POINT_AUTOMATON_LOADER, self._choose_compiled_loader)

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(snapshot.get(self.key, {}))

    def _choose_compiled_loader(self, choice) -> None:
        """Compiled when there is a package for the project's published
        revision, interpreted otherwise. The choosing is this service's: it
        is the only one that knows a package can exist at all (see
        build/compiled_automaton_loader.py)."""
        from build.compiled_automaton_loader import CompiledAutomatonLoader

        choice.replace(
            CompiledAutomatonLoader(choice.db, choice.apps_dir, session_manager=choice.session_manager),
            self.key,
        )

    def register_controllers(self, controllers: list) -> None:
        # XXX Compiled automaton requirement - do not touch.
        # XXX The Build view's Target step, wired to a real compile. The
        # directory it writes into is the one CompiledAutomatonLoader reads
        # from — one setting (build-service.apps-dir), never two.
        from build import BuildService
        from build.build_controller import BuildController

        core = bus.collect(POINT_CORE_SERVICES, {})
        service = BuildService(core["db"], core["project_service"], core["apps_dir"])
        controllers.append(construct(BuildController, {**core, "build_service": service}))
        logger.info("build started — a project can be compiled into a package.")
