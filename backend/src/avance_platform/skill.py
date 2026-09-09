"""The authoring platform, as something the system finds rather than
builds.

Everything a person does to a project *other than have a conversation
with it* lives here: the editor, the benchmark screens, Manage services,
the app store, the build view, login and the user profile — 139 routes
that a compiled product has no business answering. A build that leaves
`backend/src/avance_platform/` out serves the channels it was built with
and nothing else, and there is no flag anywhere saying an editor once
existed.

Registered from `_install`, not from `start`: start() runs at boot,
before any of the services these controllers need exist; `_install` runs
when AvanceController assembles its router, which is after (see
bus.POINT_CORE_SERVICES) — the same shape webchat and whatsapp use.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

KEY = "platform"
LABEL = "Platform — editor, benchmark, admin"


def start(raw: dict, path: Path) -> None:
    bus.contribute(POINT_HTTP_CONTROLLERS, _install)


def _install(controllers: list) -> None:
    from avance_platform.app_store_controller import AppStoreController
    from avance_platform.auth_controller import AuthController
    from avance_platform.build_controller import BuildController
    from avance_platform.edit_project_controller import EditProjectController
    from avance_platform.label_project_controller import LabelProjectController
    from avance_platform.platform_controller import PlatformController
    from avance_platform.platform_service import PlatformService
    from avance_platform.settings_controller import SettingsController
    from avance_platform.user_controller import UserController
    from build import BuildService

    core = bus.collect(POINT_CORE_SERVICES, {})
    turn_service = core["turn_service"]
    project_service = core["project_service"]
    scheduler_service = core["scheduler_service"]
    db = core["db"]
    # Two facades over one set of collaborators, not two sets: a
    # revision published here is immediately what the engine loads (see
    # avance_platform/platform_service.py).
    platform_service = PlatformService(project_service)

    controllers.extend([
        PlatformController(turn_service, project_service, platform_service),
        EditProjectController(turn_service, project_service, platform_service, scheduler_service),
        # XXX Compiled automaton requirement - do not touch.
        # XXX The Build view's Target step, wired to a real compile. The
        # directory it writes into is the one CompiledAutomatonLoader
        # reads from — one setting (build-service.apps-dir), never two.
        BuildController(BuildService(db, project_service, core["apps_dir"])),
        # Labelling only: the benchmark half of that screen left with the
        # package that runs it (see testing/testing_controller.py), so a
        # build without benchmarking still annotates sessions.
        LabelProjectController(
            turn_service, project_service, platform_service, core["tracking_service"], scheduler_service,
        ),
        SettingsController(
            turn_service, project_service, platform_service, db, core["version"],
            core["test_event_broadcaster"], scheduler_service, core["services_config"],
        ),
        AuthController(core["auth_service"]),
        UserController(core["auth_service"]),
        AppStoreController(turn_service, project_service, platform_service),
    ])
    logger.info("platform started — the authoring surface is served.")


def stop() -> None:
    """Nothing to release: the controllers hold no connection of their
    own, and every service they use belongs to the core."""
