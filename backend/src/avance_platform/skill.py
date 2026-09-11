"""The authoring platform, as something the system finds rather than
builds.

Everything a person does to a project *other than have a conversation
with it* lives here: the editor, the benchmark screens, Manage services,
the app store, login and the user profile — every route a compiled
product has no business answering. The Build view is not here: it left
with the compiler that answers it (see build/skill.py). A build that leaves
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
UI_LABEL = "Platform"
UI_DESCRIPTION = "Editor, benchmark and admin for the authoring app."


def start(raw: dict, path: Path) -> None:
    bus.contribute(POINT_HTTP_CONTROLLERS, _install)


def _install(controllers: list) -> None:
    from avance_platform.platform_service import PlatformService

    core = bus.collect(POINT_CORE_SERVICES, {})
    # Two facades over one set of collaborators, not two sets: a revision
    # published through this one is immediately what the engine loads
    # (see avance_platform/platform_service.py). The service owns its own
    # controllers — this function no longer knows what they are, let
    # alone what each of them takes.
    PlatformService(core["project_service"]).install(core, controllers)
    logger.info("platform started — the authoring surface is served.")


def stop() -> None:
    """Nothing to release: the controllers hold no connection of their
    own, and every service they use belongs to the core."""
