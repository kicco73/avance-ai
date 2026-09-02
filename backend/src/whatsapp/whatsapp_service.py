"""WhatsApp as an alternative chat client (see docs/WHATSAPP.md).

Sits beside ChatWindow.vue/WsAdapter as one more front to the very same
ChatService.process_turn: an inbound text from a linked number becomes a
turn on that account's own current live session (its active project,
its sessions, its Terms acceptance — nothing WhatsApp-specific is
persisted), and every assistant message the turn produced goes back out
through the Cloud API.

Identity: the login wall is cookie/JWT based and never sees Meta's
webhook, so the account is resolved from the sender's number through
that User row's own whatsapp_phone_number field (set either from that
user's own Profile page on the web, or by registering straight from
WhatsApp — see AuthService.register_via_whatsapp) and impersonated
(Session().impersonate) for the duration of the turn — the same
ContextVar-backed Session() every service reads the current user from.
An unlinked number whose message doesn't resolve to a valid invite code
either, or an unregistered one, only ever gets a canned reply.

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

from auth.auth_service import AuthService
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
    action_id: str | None = None


# Canned replies for everything that never reaches the automaton. The
# companion itself speaks whatever the project's prompts say; these are
# channel-level notices, so they stay short and language-neutral-ish.
REPLY_NOT_LINKED = (
    "This number isn't linked to any account. "
    "Open your profile on the web and add this WhatsApp number."
)
REPLY_NOT_REGISTERED = "Your account isn't registered yet: sign in on the web and accept the terms."
REPLY_TERMS_PENDING = "You need to accept the project's terms on the web before chatting."
REPLY_PAUSED = "This project is currently paused. Please try again later."
REPLY_UNSUPPORTED = "For now I can only read text messages."
REPLY_NO_CHAT_STATE = "The conversation doesn't accept messages at this point. Continue from the web."
REPLY_REGISTERED = "You're all set! Registration complete — you can start chatting now."
REPLY_INVALID_ACTION = "That option is no longer available. Please choose one of these instead."
REPLY_BUSY = "Please wait a moment and try again."
REPLY_DONE = "Done."
REPLY_OPTIONS_PROMPT = "What would you like to do?"

_MAX_REPLY_BUTTONS = 3
_MAX_LIST_ROWS = 10
_BUTTON_TITLE_LIMIT = 20
_LIST_ROW_TITLE_LIMIT = 24
_LIST_ROW_DESCRIPTION_LIMIT = 72
_INTERACTIVE_BODY_LIMIT = 1024
_LIST_BUTTON_TEXT = "Options"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class WhatsAppService(object):
    def __init__(
        self,
        config: WhatsAppServiceConfig,
        chat_service: ChatService,
        db: Db,
        auth_service: AuthService,
        client: WhatsAppCloudApiClient | None = None,
    ) -> None:
        self._config = config
        self._chat_service = chat_service
        self._db = db
        self._auth_service = auth_service
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
                    msg_type = msg.get("type") or ""
                    text = (msg.get("text") or {}).get("body") if msg_type == "text" else None
                    action_id = None
                    if msg_type == "interactive":
                        interactive = msg.get("interactive") or {}
                        reply = interactive.get("button_reply") or interactive.get("list_reply")
                        action_id = reply.get("id") if reply else None
                    out.append(IncomingMessage(
                        id=message_id, sender=sender, type=msg_type, text=text, action_id=action_id,
                    ))
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
                texts, manual_actions, session_id = await self._replies_for(message)
                await self._send_replies(message.sender, texts, manual_actions, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"WhatsApp: unhandled error on message {message.id} from {message.sender}: {exc}")

    async def _replies_for(self, message: IncomingMessage) -> tuple[list[str], list[dict] | None, int | None]:
        user = self._db.get_user_by_whatsapp_phone_number(message.sender)
        if user is None:
            return await self._handle_unlinked(message)
        if user.get("role") in (None, "pending"):
            return [REPLY_NOT_REGISTERED], None, None

        with Session().impersonate(user["id"]):
            Session().role = user["role"]
            Session().channel = "whatsapp-chat"
            if message.type == "interactive" and message.action_id:
                logger.info(f"WhatsApp: action '{message.action_id}' received ({message.id}) from {message.sender}.")
                return await self._run_action(message.action_id)
            if message.type != "text" or not (message.text or "").strip():
                return [REPLY_UNSUPPORTED], None, None
            return await self._run_turn(message.text.strip())

    async def _handle_unlinked(self, message: IncomingMessage) -> tuple[list[str], list[dict] | None, int | None]:
        """A number with no User row at all: the only way forward is a
        "share project" invite code (see ShareProjectDialog.vue's own
        WhatsApp QR) sent as plain text — AuthService.register_via_whatsapp
        raises PermissionError with the exact same reasons the web's own
        invite-acceptance flow does, reused verbatim as the reply here.
        Anything else (non-text, or text that isn't a real invite code at
        all) falls back to the generic "not linked" notice."""
        if message.type != "text" or not (message.text or "").strip():
            logger.info(f"WhatsApp: message from unlinked number {message.sender} ignored.")
            return [REPLY_NOT_LINKED], None, None
        try:
            self._auth_service.register_via_whatsapp(message.sender, message.text.strip())
        except PermissionError as exc:
            logger.info(f"WhatsApp: registration attempt from {message.sender} refused: {exc}")
            return [str(exc)], None, None
        logger.info(f"WhatsApp: {message.sender} registered via invite code.")
        user = self._db.get_user_by_whatsapp_phone_number(message.sender)
        with Session().impersonate(user["id"]):
            Session().role = user["role"]
            Session().channel = "whatsapp-chat"
            welcome_texts, manual_actions, session_id = await self._welcome_replies()
            return [REPLY_REGISTERED, *welcome_texts], manual_actions, session_id

    async def _welcome_replies(self) -> tuple[list[str], list[dict] | None, int | None]:
        """Right after a brand-new WhatsApp registration: same session
        bootstrap a real turn starts with, minus process_turn — there's no
        user message to run, just whatever opening message the project
        itself produces (if any), same as a fresh web registration seeing
        it immediately on landing rather than only after its first reply."""
        session_payload = self._chat_service.get_or_create_current_session(None)
        if session_payload.get("paused") or session_payload.get("legal_terms_pending"):
            return [], None, None
        session_id = session_payload["id"]
        await self._chat_service.get_messages(session_id)
        state = self._chat_service.get_state_for_session(session_id)
        return self._new_assistant_replies(session_id, last_seen_id=0), state["manual_actions"], session_id

    async def _run_turn(self, text: str) -> tuple[list[str], list[dict] | None, int | None]:
        """Mirrors ChatWindow.vue's own bootstrap: resolve the current
        live session, let open_if_needed produce any opening message,
        then the turn itself. Rather than reassembling the reply from
        process_turn's streaming-oriented result, every assistant message
        persisted since we started is what gets sent — that also covers a
        transition's follow-up messages (action_prompt / opening turn)."""
        session_payload = self._chat_service.get_or_create_current_session(None)
        if session_payload.get("paused"):
            return [REPLY_PAUSED], None, None
        if session_payload.get("legal_terms_pending"):
            return [REPLY_TERMS_PENDING], None, None
        session_id = session_payload["id"]

        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        notice: str | None = None
        manual_actions: list[dict] | None = None
        try:
            await self._chat_service.get_messages(session_id)
            reply = await self._chat_service.process_turn(session_id, text)
            manual_actions = reply["state"]["manual_actions"]
        except ServiceError as exc:
            if exc.status_code == HTTPStatus.CONFLICT:
                # A non-chat/final state, or a superseded session: nothing
                # to generate, tell the user where the conversation stands.
                notice = REPLY_NO_CHAT_STATE
            else:
                logger.exception(f"WhatsApp: turn failed on session {session_id}: {exc.message}")
                notice = "There was a problem processing your message. Please try again."

        return self._new_assistant_replies(session_id, last_seen_id, notice), manual_actions, session_id

    async def _run_action(self, action_id: str) -> tuple[list[str], list[dict] | None, int | None]:
        session_payload = self._chat_service.get_or_create_current_session(None)
        if session_payload.get("paused"):
            return [REPLY_PAUSED], None, None
        if session_payload.get("legal_terms_pending"):
            return [REPLY_TERMS_PENDING], None, None
        session_id = session_payload["id"]

        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        try:
            result = await self._chat_service.apply_manual_action(action_id, session_id)
        except ValueError as exc:
            logger.info(f"WhatsApp: action '{action_id}' rejected for session {session_id}: {exc}")
            state = self._chat_service.get_state_for_session(session_id)
            return [REPLY_INVALID_ACTION], state["manual_actions"], session_id
        except ServiceError as exc:
            if exc.status_code == HTTPStatus.CONFLICT:
                logger.info(f"WhatsApp: action '{action_id}' deferred for session {session_id}: {exc.message}")
                return [REPLY_BUSY], None, session_id
            logger.exception(f"WhatsApp: action '{action_id}' failed for session {session_id}: {exc.message}")
            notice = "There was a problem processing your message. Please try again."
            return self._new_assistant_replies(session_id, last_seen_id, notice), None, session_id

        logger.info(f"WhatsApp: action '{action_id}' applied for session {session_id}.")
        state = result["state"]
        replies = self._new_assistant_replies(session_id, last_seen_id)
        if not replies:
            replies = [state["ui_label"] or REPLY_DONE]
        return replies, state["manual_actions"], session_id

    def _new_assistant_replies(self, session_id: int, last_seen_id: int, notice: str | None = None) -> list[str]:
        replies = [
            to_whatsapp_markdown(m["content"])
            for m in self._db.get_messages(session_id)
            if m["id"] > last_seen_id and m["role"] == "assistant" and (m["content"] or "").strip()
        ]
        if notice:
            replies.append(notice)
        return replies

    # ----------------------------------------------------------------- #
    # Outbound delivery — plain text, or the last message as buttons/list
    # ----------------------------------------------------------------- #
    async def _send_replies(
        self, to: str, texts: list[str], manual_actions: list[dict] | None, session_id: int | None,
    ) -> None:
        texts = [t for t in texts if t]
        if not manual_actions:
            for text in texts:
                await self._client.send_text(to, text)
            return
        *leading, last = texts or [REPLY_DONE]
        for text in leading:
            await self._client.send_text(to, text)
        await self._send_with_buttons(to, last, manual_actions, session_id)

    async def _send_with_buttons(
        self, to: str, body: str, manual_actions: list[dict], session_id: int | None,
    ) -> None:
        if len(body) > _INTERACTIVE_BODY_LIMIT:
            await self._client.send_text(to, body)
            body = REPLY_OPTIONS_PROMPT

        actions = manual_actions
        if len(actions) > _MAX_LIST_ROWS:
            logger.warning(f"WhatsApp: state has {len(actions)} manual actions, sending only the first {_MAX_LIST_ROWS}.")
            actions = actions[:_MAX_LIST_ROWS]

        action_names = [a["name"] for a in actions]
        if len(actions) <= _MAX_REPLY_BUTTONS:
            logger.info(f"WhatsApp: sending buttons for session {session_id}: {action_names}.")
            buttons = [(a["name"], _truncate(a["ui_button"], _BUTTON_TITLE_LIMIT)) for a in actions]
            await self._client.send_buttons(to, body, buttons)
        else:
            logger.info(f"WhatsApp: sending list for session {session_id}: {action_names}.")
            rows = [
                (
                    a["name"],
                    _truncate(a["ui_button"], _LIST_ROW_TITLE_LIMIT),
                    _truncate(a["ui_description"], _LIST_ROW_DESCRIPTION_LIMIT) if a["ui_description"] else None,
                )
                for a in actions
            ]
            await self._client.send_list(to, body, _LIST_BUTTON_TEXT, rows)


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
