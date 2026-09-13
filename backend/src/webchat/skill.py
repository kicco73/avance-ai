"""The native chat, as something the platform finds rather than builds.

Nothing outside this package names it: not main.py, not controller.py,
not the socket it listens on. A build that leaves `backend/src/webchat/`
out has a running system with an open /api/core/bus that nobody
answers a turn on, and no human takeover — and nothing left in the code
saying either ever existed.

It has no HTTP surface at all any more: a conversation lives on the
bus (see docs/BUS.md), and what addresses a session by id belongs to the
core. What it contributes arrives from `register_controllers`, not from
`start_service`: starting runs at boot, before the turn engine exists;
the router is assembled after (see bus.POINT_CORE_SERVICES).
"""
from __future__ import annotations

from system import bus
from system.bus import POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class WebchatSkill(Skill):

    # No `key`: Skill derives it from the package name (see
    # skills.Skill.__init_subclass__), and the package name is the one
    # name this channel has — the skill key, the route segment, and the
    # value in CoreSession.channel are all "webchat".
    ui_label = "Web chat"
    ui_description = "Turns over the browser WebSocket."

    def register_controllers(self, controllers: list) -> None:
        from webchat.webchat_service import WebchatService

        core = bus.collect(POINT_CORE_SERVICES, {})
        service = WebchatService(
            core["turn_service"], core["project_service"], core["bus_channel"], core["db"],
        )
        # The channel this skill *is*, named once, here, where the name
        # lives (see BusChannel.owned_by). A session opened over the
        # browser socket is stamped with it, and nothing else in the
        # process has to know it.
        core["bus_channel"].owned_by(self.key)
        service.register()
        core["tracking_service"].set_human_talker_factory(service.human_talker_factory)
        logger.info("webchat started — a turn typed into the browser has somewhere to go.")
