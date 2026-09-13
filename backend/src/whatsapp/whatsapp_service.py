from __future__ import annotations

import httpx

from auth.auth_service import AuthService
from db import Db
from system import bus
from system.bus import (
    OUTPUT_ERROR, OUTPUT_SPEECH, OUTPUT_TEXT, POINT_CORE_SERVICES, POINT_SPOKEN_REPLY,
    SESSION_BLOCKED, SESSION_ENDED, SESSION_INFO, STATE_BUTTONS, Message,
)
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from system.wiring import construct
from talker import AiTalker
from turn.turn_service import TurnService
from whatsapp import notices
from whatsapp.cloud_api_client import WhatsAppCloudApiClient
from whatsapp.config import WhatsAppServiceConfig
from whatsapp.conversation import CHANNEL, Conversation
from whatsapp.identity import Identities
from whatsapp.markdown import to_whatsapp_markdown
from whatsapp.outbound import Outbound
from whatsapp.replies import Reply, TextReply, VoicePolicy
from whatsapp.voice_notes import VoiceNoteSynthesizer
from whatsapp.webhook import IncomingMessage

logger = LoggerFactory.get_logger(__name__)


class WhatsAppService(object):

    def __init__(
        self,
        config: WhatsAppServiceConfig,
        turn_service: TurnService,
        db: Db,
        auth_service: AuthService,
        client: WhatsAppCloudApiClient | None = None,
    ) -> None:
        self._config = config
        self._turn_service = turn_service
        self._db = db
        self._identities = Identities(db, auth_service)
        self._client = client or WhatsAppCloudApiClient(
            config.access_token, config.phone_number_id, config.graph_version,
        )
        self._outbound = Outbound(self._client)
        self._voice_notes = VoiceNoteSynthesizer(AiTalker())
        self._voice = VoicePolicy(config.voice_replies, self._voice_notes)
        self._conversations: dict[str, Conversation] = {}

    def listen(self, controllers: list) -> None:
        from whatsapp.whatsapp_controller import WhatsAppController

        bus.subscribe(SESSION_INFO, self._informed)
        bus.subscribe(SESSION_BLOCKED, self._refused)
        bus.subscribe(SESSION_ENDED, self._ended)
        bus.subscribe(STATE_BUTTONS, self._offered)
        bus.subscribe(OUTPUT_SPEECH, self._announced)
        bus.subscribe(OUTPUT_TEXT, self._said)
        bus.subscribe(OUTPUT_TEXT, self._unsolicited)
        bus.subscribe(OUTPUT_ERROR, self._failed)
        bus.contribute(POINT_SPOKEN_REPLY, self._spoken_reply)
        controllers.append(construct(WhatsAppController, {
            "whatsapp_service": self, "whatsapp_config": self._config,
        }))

    async def close(self) -> None:
        bus.unsubscribe(SESSION_INFO, self._informed)
        bus.unsubscribe(SESSION_BLOCKED, self._refused)
        bus.unsubscribe(SESSION_ENDED, self._ended)
        bus.unsubscribe(STATE_BUTTONS, self._offered)
        bus.unsubscribe(OUTPUT_SPEECH, self._announced)
        bus.unsubscribe(OUTPUT_TEXT, self._said)
        bus.unsubscribe(OUTPUT_TEXT, self._unsolicited)
        bus.unsubscribe(OUTPUT_ERROR, self._failed)
        bus.withdraw(POINT_SPOKEN_REPLY, self._spoken_reply)
        self._voice_notes.cancel()
        await self._client.close()

    # --- what Meta POSTs ------------------------------------------------- #

    async def receive(self, incoming: IncomingMessage) -> None:
        try:
            await self._marked_read(incoming)
            await self._identities.of(incoming).arrived(self, incoming)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"WhatsApp: unhandled error on message {incoming.id} from {incoming.sender}: {exc}"
            )
            await self._apologize(incoming.sender)

    async def converse(self, user: dict, incoming: IncomingMessage) -> None:
        await self._conversation_of(incoming.sender, user).receive(incoming)

    async def say(self, sender: str, text: str) -> None:
        await self._outbound.say(sender, Reply(text), TextReply())

    def _conversation_of(self, sender: str, user: dict) -> Conversation:
        for conversation in filter(None, [self._conversations.get(sender)]):
            return conversation
        conversation = Conversation(
            sender, user, self._outbound, self._client, self._voice, self._turn_service, self._db,
        )
        self._conversations[sender] = conversation
        return conversation

    async def _marked_read(self, incoming: IncomingMessage) -> None:
        for _ in filter(None, [self._config.mark_read]):
            await self._client.mark_read_and_show_typing(incoming.id)

    async def _apologize(self, sender: str) -> None:
        try:
            await self._client.send_text(sender, notices.TECHNICAL_PROBLEM)
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp: could not even apologize to {sender}: {exc}")

    # --- what the Bus says back ------------------------------------------ #

    async def _informed(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.informed(message.body or {}, message.session_id)

    async def _refused(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.refused(message.body or {})

    async def _ended(self, message: Message) -> None:
        for conversation in self._watching(message.session_id):
            await conversation.ended()

    async def _offered(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.offered(message.body or {})

    async def _announced(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.announced(message.body or {})

    async def _said(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.said_back(message.body or {})

    async def _failed(self, message: Message) -> None:
        for conversation in self._mine(message):
            await conversation.failed(message.body or {})

    def _spoken_reply(self, spoken) -> None:
        for conversation in self._watching(spoken.session_id):
            conversation.spoken_reply(spoken)

    def _mine(self, message: Message) -> list[Conversation]:
        for conversation in filter(None, [self._conversations.get(str(message.origin_id))]):
            return [conversation]
        return self._watching(message.session_id)

    def _watching(self, session_id: int | None) -> list[Conversation]:
        return [c for c in self._conversations.values() if c.watching(session_id)]

    # --- what nobody asked for ------------------------------------------- #

    async def _unsolicited(self, message: Message) -> None:
        for addressed in filter(_addressed_here, [message]):
            body = addressed.body if isinstance(addressed.body, dict) else {"text": addressed.body}
            await self.send_message(
                str(addressed.username), str(body.get("text") or ""), str(addressed.project_id),
            )

    async def send_message(self, phone_number: str, message_md: str, project_id: str) -> bool:
        normalized = phone_number.strip().lstrip("+")
        user = self._db.get_user_by_whatsapp_phone_number(normalized)
        if user is None:
            logger.info(f"WhatsApp: task.whatsapp to unregistered number {phone_number} — not sent.")
            return False
        try:
            await self._client.send_text(normalized, to_whatsapp_markdown(message_md))
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp: task.whatsapp to {phone_number} failed: {exc}")
            return False
        try:
            WebSession().channel = CHANNEL
            await self._turn_service.record_unsolicited_reply(user["id"], project_id, message_md)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"WhatsApp: task.whatsapp sent to {phone_number} but session logging failed: {exc}")
        return True


class NoWhatsApp(object):

    def __init__(self, config: WhatsAppServiceConfig | None) -> None:
        self._config = config

    def install(self) -> None:
        logger.info("whatsapp-service is not enabled — no webhook, no channel.")

    def listen(self, controllers: list) -> None:
        pass

    async def uninstall(self) -> None:
        pass


class WhatsApp(NoWhatsApp):

    def __init__(self, config: WhatsAppServiceConfig | None) -> None:
        super().__init__(config)
        self._service: WhatsAppService | None = None

    def install(self) -> None:
        logger.info("whatsapp-service started.")

    def listen(self, controllers: list) -> None:
        core = bus.collect(POINT_CORE_SERVICES, {})
        self._service = WhatsAppService(
            self._config, core["turn_service"], core["db"], core["auth_service"],
        )
        self._service.listen(controllers)
        logger.info("whatsapp listening — a message from a linked number reaches a turn.")

    async def uninstall(self) -> None:
        for service in filter(None, [self._service]):
            await service.close()
        self._service = None


_INSTALLATIONS = {False: NoWhatsApp, True: WhatsApp}


def installation(config: WhatsAppServiceConfig | None) -> NoWhatsApp:
    return _INSTALLATIONS[bool(config)](config)


def _addressed_here(message: Message) -> bool:
    return message.channel == CHANNEL and message.session_id is None
