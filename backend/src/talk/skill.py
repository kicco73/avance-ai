from __future__ import annotations

from pathlib import Path

import bus
from bus import POINT_CONFIG_SERVICES, POINT_TALK_PROVIDER
from logging_factory import LoggerFactory
from talk import config as talk_config
from talk.talk_service import TalkService

logger = LoggerFactory.get_logger(__name__)

KEY = "talk"
LABEL = "Talk — text to speech"


def start(raw: dict, path: Path, scheduler_service) -> None:
    services = talk_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update({KEY: talk_config.public_fields(services)}))
    if services is None:
        logger.info("talk-service is not enabled — no audio.")
        return

    service = TalkService.from_config(services)
    bus.contribute(POINT_TALK_PROVIDER, lambda registry: registry.update({"generate": service.generate}))
    logger.info("talk-service started with %d provider(s).", len(services))


def stop() -> None:
    pass
