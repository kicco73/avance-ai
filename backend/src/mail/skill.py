from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import MAIL_SEND, POINT_CONFIG_SERVICES, POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from mail import config as mail_config
from mail.mail_service import MailService

logger = LoggerFactory.get_logger(__name__)

KEY = "mail"
LABEL = "Mail"

_listener = None


def start(raw: dict, path: Path) -> None:
    config = mail_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update({KEY: mail_config.public_fields(config)}))
    if config is None:
        logger.info("mail-service is not enabled — task.send_mail can't run.")
        return

    # Built on the first mail rather than here: MailService needs the
    # scheduler, which does not exist at boot. By the time anything
    # publishes MAIL_SEND the core is composed, so this is the whole
    # difference between "needs a core object" and "takes one as a
    # parameter" (see bus.POINT_CORE_SERVICES).
    service = None

    async def on_mail_send(message: bus.Message) -> None:
        nonlocal service
        if service is None:
            service = MailService(config, bus.collect(POINT_CORE_SERVICES, {})["scheduler_service"])
        body = message.body
        service.enqueue_mail(body["to"], body["subject"], body["body_md"])

    global _listener
    _listener = on_mail_send
    bus.subscribe(MAIL_SEND, on_mail_send)
    logger.info("mail-service started.")


def stop() -> None:
    if _listener is not None:
        bus.unsubscribe(MAIL_SEND, _listener)
