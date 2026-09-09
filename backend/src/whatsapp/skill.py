"""WhatsApp as something the platform finds rather than builds.

Everything this package contributes is registered from here, and nothing
outside it names WhatsApp: not main.py, not config.py, not the composition
root. A build that leaves `backend/src/whatsapp/` out has no WhatsApp
channel, and there is nothing else to switch off.

What it registers:
  - its own `whatsapp-service` section, parsed by whatsapp/config.py
  - Meta's two webhook routes (whatsapp/whatsapp_controller.py)
  - the sender behind task.whatsapp() (bus.POINT_WHATSAPP_SENDER)
  - the wa.me link an invite carries (bus.POINT_INVITE_LINKS)
  - its section of Settings > Manage services
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from system import bus
from system.bus import (
    POINT_CONFIG_SERVICES,
    POINT_CORE_SERVICES,
    POINT_HTTP_CONTROLLERS,
    POINT_INVITE_LINKS,
    POINT_WHATSAPP_SENDER,
)
from system.logging_factory import LoggerFactory
from whatsapp import config as whatsapp_config

logger = LoggerFactory.get_logger(__name__)

KEY = "whatsapp"
LABEL = "WhatsApp — chat channel"

_config = None
_service = None


def start(raw: dict, path: Path) -> None:
    global _config
    _config = whatsapp_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update(
        {KEY: whatsapp_config.public_fields(_config)}
    ))
    if _config is None:
        logger.info("whatsapp-service is not enabled — no webhook, no channel.")
        return

    bus.contribute(POINT_HTTP_CONTROLLERS, _install_controller)
    bus.contribute(POINT_WHATSAPP_SENDER, lambda registry: registry.update({"send_message": _send_message}))
    if _config.phone_number:
        bus.contribute(POINT_INVITE_LINKS, lambda links: links.update({"whatsapp_url": _invite_url}))
    logger.info("whatsapp-service started.")


def _service_now():
    """The one WhatsAppService, built the first time anything actually
    needs it — never at start(), when the core it talks to (TurnService,
    Db, AuthService) does not exist yet (see bus.POINT_CORE_SERVICES)."""
    global _service
    if _service is None:
        from whatsapp.whatsapp_service import WhatsAppService

        core = bus.collect(POINT_CORE_SERVICES, {})
        _service = WhatsAppService(_config, core["turn_service"], core["db"], core["auth_service"])
    return _service


def _install_controller(controllers: list) -> None:
    from whatsapp.whatsapp_controller import WhatsAppController

    controllers.append(WhatsAppController(_service_now()))


async def _send_message(phone_number: str, message_md: str, project_id: str) -> bool:
    return await _service_now().send_message(phone_number, message_md, project_id)


def _invite_url(code: str) -> str:
    return f"https://wa.me/{_config.phone_number}?text={quote(_config.invite_prefix + code)}"


async def stop() -> None:
    global _service
    if _service is not None:
        await _service.close()
        _service = None
