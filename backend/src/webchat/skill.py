"""The native chat, as something the platform finds rather than builds.

Nothing outside this package names it: not main.py, not controller.py,
not the socket it listens on. A build that leaves `backend/src/webchat/`
out has a running system with an open /ws/notifications that nobody
answers a turn on, and no human takeover — and nothing left in the code
saying either ever existed.

Registered from `_install`, not from `start`: start() runs at boot,
before the turn engine exists; `_install` runs when AvanceController
assembles its router, which is after (see bus.POINT_CORE_SERVICES).
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

KEY = "webchat"
LABEL = "Native chat — turns over the browser socket"


def start(raw: dict, path: Path) -> None:
    bus.contribute(POINT_HTTP_CONTROLLERS, _install)


def _install(controllers: list) -> None:
    from webchat.webchat_service import WebchatService

    core = bus.collect(POINT_CORE_SERVICES, {})
    service = WebchatService(
        core["turn_service"], core["project_service"], core["ws_notifications"],
    )
    service.register()
    core["tracking_service"].set_human_talker_factory(service.human_talker_factory)
    controllers.append(service.controller)
    logger.info("webchat started — a turn typed into the browser has somewhere to go.")


def stop() -> None:
    """Nothing to release: the subscriptions live as long as the process,
    and the connections belong to system.ws_notifications."""
