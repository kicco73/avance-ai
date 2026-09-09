from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_CONFIG_SERVICES, POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS, POINT_TALK_PROVIDER
from system.logging_factory import LoggerFactory
from talk import config as talk_config
from talk.talk_service import TalkService

logger = LoggerFactory.get_logger(__name__)

KEY = "talk"
LABEL = "Talk — text to speech"


def start(raw: dict, path: Path) -> None:
    services = talk_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update({KEY: talk_config.public_fields(services)}))
    if services is None:
        logger.info("talk-service is not enabled — no audio.")
        return

    service = TalkService.from_config(services)
    bus.contribute(POINT_TALK_PROVIDER, lambda registry: registry.update({"generate": service.generate}))
    # The audio route, which used to be written into the core's own chat
    # controller and answered 503 in a build without this package. Now
    # it is not there to answer at all (see talk/talk_controller.py).
    bus.contribute(POINT_HTTP_CONTROLLERS, _install_controller)
    logger.info("talk-service started with %d provider(s).", len(services))


def _install_controller(controllers: list) -> None:
    from talk.talk_controller import TalkController

    core = bus.collect(POINT_CORE_SERVICES, {})
    controllers.append(TalkController(core["turn_service"]))


def stop() -> None:
    pass
