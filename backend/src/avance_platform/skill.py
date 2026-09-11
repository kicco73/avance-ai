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

Its controllers arrive from `register_controllers`, not from
`start_service`: starting runs at boot, before any of the services these
controllers need exist; the router is assembled after (see
bus.POINT_CORE_SERVICES) — the same shape webchat and whatsapp use.
"""
from __future__ import annotations

from system import bus
from system.bus import POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class PlatformSkill(Skill):

    key = "platform"
    ui_label = "Platform"
    ui_description = "Editor, benchmark and admin for the authoring app."

    def register_controllers(self, controllers: list) -> None:
        from avance_platform.platform_service import PlatformService

        core = bus.collect(POINT_CORE_SERVICES, {})
        # Two facades over one set of collaborators, not two sets: a revision
        # published through this one is immediately what the engine loads
        # (see avance_platform/platform_service.py). The service owns its own
        # controllers — this function no longer knows what they are, let
        # alone what each of them takes.
        PlatformService(core["project_service"]).install(core, controllers)
        logger.info("platform started — the authoring surface is served.")
