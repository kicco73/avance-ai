"""WhatsApp as an alternative chat client (see docs/WHATSAPP.md).

Sits beside ChatWindow.vue/WsAdapter as one more front to the very same
ChatService.process_turn: an inbound text from a linked number becomes a
turn on that account's own current live session (its active project,
its sessions, its Terms acceptance — nothing WhatsApp-specific is
persisted), and every assistant message the turn produced goes back out
through the Cloud API.

Identity: the login wall is cookie/JWT based and never sees Meta's
webhook, so the account is resolved from the sender's number through
the `whatsapp-service.users` mapping in .config.yml and impersonated
(Session().impersonate) for the duration of the turn — the same
ContextVar-backed Session() every service reads the current user from.
An unlisted or unregistered number only ever gets a canned reply.

Ordering: Meta may deliver two messages from the same person back to
back; a per-sender asyncio.Lock keeps their turns sequential (on top of
ChatService's own per-session lock, which would otherwise just make the
second one wait in whichever order the event loop picked).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from http import HTTPStatus

from chat.chat_service import ChatService
from config import WhatsAppServiceConfig
from db import Db
from logging_factory import LoggerFactory
from service_error import ServiceError
from session import Session
from whatsapp.cloud_api_client import WhatsAppCloudApiClient

logger = LoggerFactory.get_logger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    id: str
    sender: str  # E.164 digits, no '+', as Meta sends it
    type: str
    text: str | None


# Canned replies for everything that never reaches the automaton. The
# companion itself speaks whatever the project's prompts say; these are
# channel-level notices, so they stay short and language-neutral-ish.
REPLY_NOT_LINKED = (
    "Este número no está vinculado a ninguna cuenta. "
    "Pide al administrador que lo añada a la configuración del servicio."
)
REPLY_NOT_REGISTERED = "Tu cuenta aún no está registrada: entra desde la web y acepta los términos."
REPLY_TERMS_PENDING = "Antes de chatear tienes que aceptar los términos del proyecto desde la web."
REPLY_PAUSED = "Este proyecto está pausado en este momento. Inténtalo más tarde."
REPLY_UNSUPPORTED = "Por ahora solo puedo leer mensajes de texto."
REPLY_NO_CHAT_STATE = "En este punto la conversación no acepta mensajes. Continúa desde la web."


class WhatsAppService(object):
    def __init__(
        self,
        config: WhatsAppServiceConfig,
        chat_service: ChatService,
        db: Db,
        client: WhatsAppCloudApiClient | None = None,
    ) -> None:
        self._config = config
        self._chat_service = chat_service
        self._db = db
        self._client = client or WhatsAppCloudApiClient(
            config.access_token, config.phone_number_id, config.graph_version
        )
        self._seen = _SeenMessages()
        self._sender_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self._client.close()

    # ----------------------------------------------------------------- #
    # Webhook plumbing (used by WhatsAppController)
    # ----------------------------------------------------------------- #
    def is_valid_verify_token(self, token: str) -> bool:
        return hmac.compare_digest(token, self._config.verify_token)

    def is_valid_signature(self, raw_body: bytes, header: str | None) -> bool:
        """X-Hub-Signature-256: 'sha256=' + HMAC-SHA256(app secret, raw body)."""
        if not header or not header.startswith("sha256="):
            return False
        expected = hmac.new(self._config.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, header[len("sha256="):])

    @staticmethod
    def extract_incoming(payload: dict) -> list[IncomingMessage]:
        """Every inbound message in a webhook payload. Status updates
        (delivered/read receipts) share the same envelope but live under
        `statuses`, not `messages`, so they simply never show up here."""
        out: list[IncomingMessage] = []
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                if value.get("messaging_product") != "whatsapp":
                    continue
                for msg in value.get("messages", []) or []:
                    message_id, sender = msg.get("id"), msg.get("from")
                    if not message_id or not sender:
                        continue
                    text = (msg.get("text") or {}).get("body") if msg.get("type") == "text" else None
                    out.append(IncomingMessage(id=message_id, sender=sender, type=msg.get("type") or "", text=text))
        return out

    def accept(self, message: IncomingMessage) -> bool:
        """False for a redelivery of an already-handled message (Meta
        retries whenever the webhook didn't answer 200 fast enough)."""
        return self._seen.check_and_add(message.id)

    # ----------------------------------------------------------------- #
    # Turn handling
    # ----------------------------------------------------------------- #
    async def handle(self, message: IncomingMessage) -> None:
        """Runs after the webhook already answered 200 — must never raise."""
        try:
            if self._config.mark_read:
                await self._client.mark_read(message.id)
            lock = self._sender_locks.setdefault(message.sender, asyncio.Lock())
            async with lock:
                for reply in await self._replies_for(message):
                    if reply:
                        await self._client.send_text(message.sender, reply)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"WhatsApp: unhandled error on message {message.id} from {message.sender}: {exc}")

    async def _replies_for(self, message: IncomingMessage) -> list[str]:
        email = self._config.users.get(message.sender)
        if email is None:
            logger.info(f"WhatsApp: message from unlinked number {message.sender} ignored.")
            return [REPLY_NOT_LINKED]
        user = self._db.get_user_by_id(email)
        if user is None or user.get("role") in (None, "pending"):
            return [REPLY_NOT_REGISTERED]
        if message.type != "text" or not (message.text or "").strip():
            return [REPLY_UNSUPPORTED]

        with Session().impersonate(email):
            Session().role = user["role"]
            return await self._run_turn(message.text.strip())

    async def _run_turn(self, text: str) -> list[str]:
        """Mirrors ChatWindow.vue's own bootstrap: resolve the current
        live session, let open_if_needed produce any opening message,
        then the turn itself. Rather than reassembling the reply from
        process_turn's streaming-oriented result, every assistant message
        persisted since we started is what gets sent — that also covers a
        transition's follow-up messages (action_prompt / opening turn)."""
        session_payload = self._chat_service.get_or_create_current_session(None)
        if session_payload.get("paused"):
            return [REPLY_PAUSED]
        if session_payload.get("legal_terms_pending"):
            return [REPLY_TERMS_PENDING]
        session_id = session_payload["id"]

        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        notice: str | None = None
        try:
            await self._chat_service.get_messages(session_id)
            await self._chat_service.process_turn(session_id, text)
        except ServiceError as exc:
            if exc.status_code == HTTPStatus.CONFLICT:
                # A non-chat/final state, or a superseded session: nothing
                # to generate, tell the user where the conversation stands.
                notice = REPLY_NO_CHAT_STATE
            else:
                logger.exception(f"WhatsApp: turn failed on session {session_id}: {exc.message}")
                notice = "Ha habido un problema procesando tu mensaje. Inténtalo de nuevo."

        replies = [
            to_whatsapp_markdown(m["content"])
            for m in self._db.get_messages(session_id)
            if m["id"] > last_seen_id and m["role"] == "assistant" and (m["content"] or "").strip()
        ]
        if notice:
            replies.append(notice)
        return replies


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #
class _SeenMessages(object):
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def check_and_add(self, message_id: str) -> bool:
        now = time.monotonic()
        if len(self._seen) > 5000:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
        if message_id in self._seen and now - self._seen[message_id] < self._ttl:
            return False
        self._seen[message_id] = now
        return True


_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_BULLET = re.compile(r"^(\s*)[*+]\s+", re.MULTILINE)


def to_whatsapp_markdown(text: str) -> str:
    """The model writes CommonMark (see docs/MARKDOWN_GUIDE.md); WhatsApp
    only renders *bold*, _italic_, ~strike~, ```mono``` and '- ' lists.
    Headings become bold lines, links are spelled out, '*' bullets become
    '-' so they aren't mistaken for bold markers."""
    text = _LINK.sub(r"\1 (\2)", text)
    text = _BOLD.sub(r"*\2*", text)
    text = _HEADING.sub(lambda m: f"*{m.group(1).strip('*_ ')}*", text)
    text = _BULLET.sub(r"\1- ", text)
    return text.strip()
