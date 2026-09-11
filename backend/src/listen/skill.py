"""Listen, as something the platform finds rather than builds.

Everything this package contributes is registered from here, and nothing
outside it names Listen: not main.py, not config.py, not the chat
controller. Which is the whole point — a build that leaves
`backend/src/listen/` out has no speech-to-text, and there is nothing
else to switch off.

What it registers:
  - its own `listen-service` section, parsed by listen/config.py
  - a decoder on the Bus for `input.audio` (listen/decoder.py)
  - its own route (listen/listen_controller.py)
  - `listen_enabled` on GET /api/state, read from the service itself at
    the moment the answer is built — the model loads in a background
    thread, so this is False for the first moments after boot and True
    afterwards, without anyone caching a stale answer
  - its section of Settings > Manage services
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import INPUT_AUDIO, POINT_API_STATE, POINT_CONFIG_SERVICES, POINT_HTTP_CONTROLLERS
from system.config_services import ui_section
from listen import config as listen_config
from listen.decoder import SpeechDecoder
from listen.listen_controller import ListenController
from listen.listen_service import ListenService
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

KEY = "listen"
UI_LABEL = "Listen"
UI_DESCRIPTION = "Speech to text."


def start(raw: dict, path: Path) -> None:
    """Called once at boot with the configuration file as it was read.
    Absent or disabled section: nothing registers, and every question
    about speech-to-text answers "nobody" from then on."""
    services = listen_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update(
        {KEY: ui_section(UI_LABEL, UI_DESCRIPTION, listen_config.public_fields(services))}
    ))
    if services is None:
        logger.info("listen-service is not enabled — no decoder, no route.")
        return

    service = ListenService.from_config(services)
    SpeechDecoder(service).register()
    bus.contribute(POINT_HTTP_CONTROLLERS, lambda controllers: controllers.append(ListenController(service)))
    bus.contribute(POINT_API_STATE, lambda payload: payload.update({"listen_enabled": service.enabled}))
    logger.info("listen-service started with %d provider(s).", len(services))


def stop() -> None:
    """Nothing to release: the decoder holds no connection and the
    provider's own model is freed with the process."""
