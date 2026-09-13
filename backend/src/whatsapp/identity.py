from __future__ import annotations

from auth.auth_service import AuthService
from db import Db
from system.logging_factory import LoggerFactory
from whatsapp import notices
from whatsapp.webhook import IncomingMessage

logger = LoggerFactory.get_logger(__name__)


class Linked(object):

    def __init__(self, user: dict) -> None:
        self.user = user

    async def arrived(self, service, incoming: IncomingMessage) -> None:
        await service.converse(self.user, incoming)


class JustRegistered(Linked):

    async def arrived(self, service, incoming: IncomingMessage) -> None:
        await service.say(incoming.sender, notices.REGISTERED)


class Refused(object):

    def __init__(self, text: str) -> None:
        self._text = text

    async def arrived(self, service, incoming: IncomingMessage) -> None:
        await service.say(incoming.sender, self._text)


class Identities(object):

    def __init__(self, db: Db, auth_service: AuthService) -> None:
        self._db = db
        self._auth_service = auth_service

    def of(self, incoming: IncomingMessage) -> Linked | Refused:
        user = self._db.get_user_by_whatsapp_phone_number(incoming.sender)
        for row in filter(None, [user]):
            return self._known(row)
        return self._registering(incoming)

    def _known(self, user: dict) -> Linked | Refused:
        for _ in filter(None, [user.get("role") in (None, "pending")]):
            return Refused(notices.NOT_REGISTERED)
        return Linked(user)

    def _registering(self, incoming: IncomingMessage) -> Linked | Refused:
        code = incoming.invite_code()
        for _ in filter(None, [code is None]):
            logger.info(f"WhatsApp: message from unlinked number {incoming.sender} ignored.")
            return Refused(notices.NOT_LINKED)
        try:
            self._auth_service.register_via_whatsapp(incoming.sender, str(code))
        except PermissionError as exc:
            logger.info(f"WhatsApp: registration attempt from {incoming.sender} refused: {exc}")
            return Refused(str(exc))
        logger.info(f"WhatsApp: {incoming.sender} registered via invite code.")
        user = self._db.get_user_by_whatsapp_phone_number(incoming.sender)
        assert user is not None
        return JustRegistered(user)
