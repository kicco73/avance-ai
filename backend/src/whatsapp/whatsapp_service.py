"""WhatsApp as an alternative chat client (see docs/WHATSAPP.md).

Sits beside ChatWindow.vue/WsAdapter as one more front to the very same
ChatService.process_turn: an inbound text from a linked number becomes a
turn on that account's own current live session (its active project,
its sessions, its Terms acceptance — nothing WhatsApp-specific is
persisted), and every assistant message the turn produced goes back out
through the Cloud API.

Voice: an inbound voice note is downloaded from Meta, transcribed with
ListenService (faster-whisper reads OGG/Opus as is) and processed as if
the user had typed it — the transcript is what gets persisted as the
user's message, so the web shows it too. Replies answer in kind (config
`voice-replies`): a spoken reply back when the user spoke, text when they
typed. The spoken reply is TalkService's WAV for the reply's own [audio]
text, re-encoded to MP3 (see whatsapp/audio.py — MP3 rather than
OGG/Opus so WhatsApp shows it as an audio message, not a voice note) and uploaded;
whenever that isn't possible (no talk-service, no audio text for that
reply, an encoding/upload failure) the text goes out instead, so the
user is never left without an answer. Buttons (manual actions) ride on
a text message, so after a spoken reply they come as a short follow-up.

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
from typing import TYPE_CHECKING

import httpx

from auth.auth_service import AuthService
from chat.channels import WHATSAPP_CHAT
from chat.chat_service import ChatService
from config import WhatsAppServiceConfig
from db import Db
from logging_factory import LoggerFactory
from service_error import ServiceError
from session import Session
from whatsapp.cloud_api_client import WhatsAppCloudApiClient

if TYPE_CHECKING:
    from listen.listen_service import ListenService
    from talk.talk_service import TalkService

logger = LoggerFactory.get_logger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    id: str
    sender: str  # E.164 digits, no '+', as Meta sends it
    type: str
    text: str | None
    action_id: str | None = None
    # Meta's media id for an `audio` message (a voice note or an audio
    # file — both arrive as type "audio"); downloaded on demand.
    audio_id: str | None = None


@dataclass(frozen=True)
class Reply:
    text: str
    # The reply's own [audio] text (Message.audio_text), when the project
    # produced one — what TalkService would speak. None = text only.
    audio_text: str | None = None


# Canned replies for everything that never reaches the automaton. The
# companion itself speaks whatever the project's prompts say; these are
# channel-level notices, so they stay short and language-neutral-ish.
REPLY_NOT_LINKED = (
    "This number isn't linked to any account. "
    "Open your profile on the web and add this WhatsApp number."
)
REPLY_NOT_REGISTERED = "Your account isn't registered yet: sign in on the web and accept the terms."
REPLY_TERMS_ACCEPTED = "Thanks — terms accepted. You can continue chatting."
REPLY_PAUSED = "This project is currently paused. Please try again later."
REPLY_UNSUPPORTED = "For now I can only read text messages."
REPLY_UNSUPPORTED_AUDIO = "I can't listen to voice notes yet — please type your message."
REPLY_AUDIO_NOT_UNDERSTOOD = "I couldn't make out that voice note. Could you repeat it, or type it?"
REPLY_NO_CHAT_STATE = "The conversation doesn't accept messages at this point. Continue from the web."
REPLY_REGISTERED = "You're all set! Registration complete — you can start chatting now."
REPLY_INVALID_ACTION = "That option is no longer available. Please choose one of these instead."
REPLY_BUSY = "Please wait a moment and try again."
REPLY_SESSION_TAKEN_OVER = "This conversation continued somewhere else. Send another message to keep chatting here."
REPLY_DONE = "Done."
REPLY_OPTIONS_PROMPT = "What would you like to do?"
REPLY_TECHNICAL_PROBLEM = "We apologize for the inconvenience — a technical problem occurred. Please try again in a moment."
REPLY_ACCEPT_TERMS_LABEL = "Accept"

_MAX_REPLY_BUTTONS = 3
_MAX_LIST_ROWS = 10
_BUTTON_TITLE_LIMIT = 20
_LIST_ROW_TITLE_LIMIT = 24
_LIST_ROW_DESCRIPTION_LIMIT = 72
_INTERACTIVE_BODY_LIMIT = 1024
_LIST_BUTTON_TEXT = "Options"
# Reserved action id for the terms-acceptance button — distinct from any
# real automaton action name, dispatched before _run_action ever sees it.
_ACCEPT_TERMS_ACTION = "__whatsapp_accept_terms__"


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
        talk_service: "TalkService | None" = None,
        listen_service: "ListenService | None" = None,
    ) -> None:
        self._config = config
        self._chat_service = chat_service
        self._db = db
        self._auth_service = auth_service
        self._talk_service = talk_service
        self._listen_service = listen_service
        self._client = client or WhatsAppCloudApiClient(
            config.access_token, config.phone_number_id, config.graph_version
        )
        self._seen = _SeenMessages()
        self._sender_locks: dict[str, asyncio.Lock] = {}
        self._voice_notes = VoiceNoteSynthesizer(talk_service) if talk_service is not None else None

    async def close(self) -> None:
        if self._voice_notes is not None:
            self._voice_notes.cancel()
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
                    audio_id = (msg.get("audio") or {}).get("id") if msg_type == "audio" else None
                    action_id = None
                    if msg_type == "interactive":
                        interactive = msg.get("interactive") or {}
                        reply = interactive.get("button_reply") or interactive.get("list_reply")
                        action_id = reply.get("id") if reply else None
                    out.append(IncomingMessage(
                        id=message_id, sender=sender, type=msg_type, text=text, action_id=action_id,
                        audio_id=audio_id,
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
                await self._client.mark_read_and_show_typing(message.id)
            lock = self._sender_locks.setdefault(message.sender, asyncio.Lock())
            async with lock:
                try:
                    replies, manual_actions, session_id, spoken = await self._replies_for(message)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"WhatsApp: unexpected error resolving a reply to {message.id} from {message.sender}: {exc}")
                    await self._client.send_text(message.sender, REPLY_TECHNICAL_PROBLEM)
                    return
                await self._send_replies(
                    message.sender, replies, manual_actions, session_id, voice=self._wants_voice(spoken),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"WhatsApp: unhandled error on message {message.id} from {message.sender}: {exc}")

    async def _replies_for(self, message: IncomingMessage) -> tuple[list[Reply], list[dict] | None, int | None, bool]:
        """(replies, manual_actions, session_id, spoken) — `spoken` is
        True when the user's message came in as a voice note, which is
        what the voice-replies policy keys on."""
        user = self._db.get_user_by_whatsapp_phone_number(message.sender)
        if user is None:
            return (*await self._handle_unlinked(message), False)
        if user.get("role") in (None, "pending"):
            return _notice(REPLY_NOT_REGISTERED)

        with Session().impersonate(user["id"]):
            Session().role = user["role"]
            Session().channel = WHATSAPP_CHAT
            if message.type == "interactive" and message.action_id:
                logger.info(f"WhatsApp: action '{message.action_id}' received ({message.id}) from {message.sender}.")
                if message.action_id == _ACCEPT_TERMS_ACTION:
                    return (*await self._accept_terms_action(), False)
                return (*await self._run_action(message.action_id), False)
            if message.type == "audio" and message.audio_id:
                if self._listen_service is None:
                    logger.info(f"WhatsApp [{message.id}]: voice note from {message.sender} but no listen-service.")
                    return _notice(REPLY_UNSUPPORTED_AUDIO)
                text = await self._transcribe(message, message.audio_id, self._listen_service)
                if text is None:
                    return _notice(REPLY_AUDIO_NOT_UNDERSTOOD)
                return (*await self._run_turn(text, spoken=True), True)
            text = (message.text or "").strip()
            if message.type != "text" or not text:
                return _notice(REPLY_UNSUPPORTED)
            return (*await self._run_turn(text), False)

    async def _transcribe(self, message: IncomingMessage, audio_id: str, listen_service: "ListenService") -> str | None:
        """Transcript of the voice note, or None when it couldn't be
        fetched/understood — the caller turns that into a notice, never
        a turn with an empty user message."""
        from listen.listen_service import ListenServiceError

        try:
            audio, mime_type = await self._client.download_media(audio_id)
            transcript = (await listen_service.transcribe(audio)).strip()
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp [{message.id}]: media download failed: {exc}")
            return None
        except ListenServiceError as exc:
            logger.warning(f"WhatsApp [{message.id}]: transcription failed: {exc}")
            return None
        if not transcript:
            logger.info(f"WhatsApp [{message.id}]: empty transcript ({mime_type}, {len(audio)} bytes).")
            return None
        logger.info(f"WhatsApp [{message.id}]: transcribed {len(audio)} bytes of {mime_type}: {transcript[:80]!r}")
        return transcript

    async def _handle_unlinked(self, message: IncomingMessage) -> tuple[list[Reply], list[dict] | None, int | None]:
        """A number with no User row at all: the only way forward is a
        "share project" invite code (see ShareProjectDialog.vue's own
        WhatsApp QR) sent as plain text — AuthService.register_via_whatsapp
        raises PermissionError with the exact same reasons the web's own
        invite-acceptance flow does, reused verbatim as the reply here.
        Anything else (non-text, or text that isn't a real invite code at
        all) falls back to the generic "not linked" notice."""
        text = (message.text or "").strip()
        if message.type != "text" or not text:
            logger.info(f"WhatsApp: message from unlinked number {message.sender} ignored.")
            return [Reply(REPLY_NOT_LINKED)], None, None
        code = text.split()[-1]
        try:
            self._auth_service.register_via_whatsapp(message.sender, code)
        except PermissionError as exc:
            logger.info(f"WhatsApp: registration attempt from {message.sender} refused: {exc}")
            return [Reply(str(exc))], None, None
        logger.info(f"WhatsApp: {message.sender} registered via invite code.")
        user = self._db.get_user_by_whatsapp_phone_number(message.sender)
        assert user is not None
        with Session().impersonate(user["id"]):
            Session().role = user["role"]
            Session().channel = WHATSAPP_CHAT
            welcome_texts, manual_actions, session_id = await self._welcome_replies()
            return [Reply(REPLY_REGISTERED), *welcome_texts], manual_actions, session_id

    async def _welcome_replies(self) -> tuple[list[Reply], list[dict] | None, int | None]:
        """Right after a brand-new WhatsApp registration: same session
        bootstrap a real turn starts with, minus process_turn — there's no
        user message to run, just whatever opening message the project
        itself produces (if any), same as a fresh web registration seeing
        it immediately on landing rather than only after its first reply."""
        session_payload = await self._chat_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return [], None, None
        if session_payload.get("legal_terms_pending"):
            return self._terms_reply(session_payload["project_id"])
        session_id = session_payload["id"]
        replies, manual_actions = await self._bootstrap_replies(session_id)
        return replies, manual_actions, session_id

    def _terms_reply(self, project_id: str) -> tuple[list[Reply], list[dict] | None, int | None]:
        """The project's own legal/terms.md plus an Accept button — the
        WhatsApp equivalent of TermsView.vue, in place of the plain "go
        accept it on the web" notice this used to send."""
        status = self._chat_service.get_legal_terms_status(project_id)
        manual_actions = [{"name": _ACCEPT_TERMS_ACTION, "ui_button": REPLY_ACCEPT_TERMS_LABEL, "ui_description": None}]
        return [Reply(to_whatsapp_markdown(status["content"] or ""))], manual_actions, None

    async def _accept_terms_action(self) -> tuple[list[Reply], list[dict] | None, int | None]:
        session_payload = await self._chat_service.acquire_exclusive_session()
        if session_payload.get("legal_terms_pending"):
            self._chat_service.accept_legal_terms(session_payload["project_id"])
            session_payload = await self._chat_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return [Reply(REPLY_PAUSED)], None, None
        if session_payload.get("legal_terms_pending"):
            return self._terms_reply(session_payload["project_id"])
        session_id = session_payload["id"]
        replies, manual_actions = await self._bootstrap_replies(session_id)
        return replies or [Reply(REPLY_TERMS_ACCEPTED)], manual_actions, session_id

    async def _bootstrap_replies(self, session_id: int) -> tuple[list[Reply], list[dict] | None]:
        """New assistant content, if any, once a session is confirmed
        resolvable — a fresh session's own opening message, or nothing for
        one whose history was already delivered earlier (e.g. right after
        accepting terms mid-conversation, where the baseline is whatever
        the session already had before this call)."""
        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        await self._chat_service.get_messages(session_id)
        state = self._chat_service.get_state_for_session(session_id)
        return self._new_assistant_replies(session_id, last_seen_id), state["manual_actions"]

    async def _bootstrap_exclusive_session(self) -> tuple[int | None, tuple[list[Reply], list[dict] | None, int | None] | None]:
        """(session_id, None) once resolved, or (None, early_result) for
        the caller to return as-is (paused/terms-pending) without ever
        reaching a session_id at all."""
        session_payload = await self._chat_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return None, ([Reply(REPLY_PAUSED)], None, None)
        if session_payload.get("legal_terms_pending"):
            return None, self._terms_reply(session_payload["project_id"])
        return session_payload["id"], None

    async def _attempt_turn(
        self, session_id: int, text: str, on_metadata,
    ) -> tuple[str | None, list[dict] | None, str | None]:
        """(notice, manual_actions, retry_code) for one turn attempt —
        retry_code (session_closed/session_not_found) tells the caller to
        acquire a fresh session and attempt once more."""
        try:
            await self._chat_service.prepare_user_initiated_turn(session_id)
            reply = await self._chat_service.process_turn(session_id, text, on_metadata=on_metadata)
            return None, reply["state"]["manual_actions"], None
        except ServiceError as exc:
            if exc.code == "state_not_chat":
                state = self._chat_service.get_state_for_session(session_id)
                return REPLY_NO_CHAT_STATE, state["manual_actions"], None
            if exc.code in ("session_channel_mismatch", "session_superseded"):
                return REPLY_SESSION_TAKEN_OVER, None, None
            if exc.code == "turn_in_progress":
                return REPLY_BUSY, None, None
            if exc.code in ("session_closed", "session_not_found"):
                return None, None, exc.code
            logger.exception(f"WhatsApp: turn failed on session {session_id}: {exc.message}")
            return "There was a problem processing your message. Please try again.", None, None

    async def _run_turn(self, text: str, spoken: bool = False) -> tuple[list[Reply], list[dict] | None, int | None]:
        """Unlike ChatWindow.vue's own bootstrap, it's the user's text
        that starts or continues the conversation here — no AI-initiated
        opening message runs ahead of it (see ChatService.
        prepare_user_initiated_turn). Rather than reassembling the reply
        from process_turn's streaming-oriented result, every assistant
        message persisted since we started is what gets sent — that also
        covers a transition's follow-up messages (a chat-blocked state's
        own wrap-up message)."""
        session_id, early = await self._bootstrap_exclusive_session()
        if early is not None:
            return early

        voice_notes = self._voice_notes if self._wants_voice(spoken) else None
        on_metadata = voice_notes.on_metadata if voice_notes is not None else None

        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        notice, manual_actions, retry_code = await self._attempt_turn(session_id, text, on_metadata)
        if retry_code is not None:
            session_id, early = await self._bootstrap_exclusive_session()
            if early is not None:
                return early
            last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
            notice, manual_actions, retry_code = await self._attempt_turn(session_id, text, on_metadata)
            if retry_code is not None:
                notice = REPLY_TECHNICAL_PROBLEM

        return self._new_assistant_replies(session_id, last_seen_id, notice), manual_actions, session_id

    async def _attempt_action(
        self, action_id: str, session_id: int,
    ) -> tuple[str | None, list[dict] | None, str | None, dict | None]:
        """(notice, manual_actions, retry_code, result) for one manual
        action attempt — `result` is apply_manual_action's own return
        value, set only on success."""
        try:
            result = await self._chat_service.apply_manual_action(action_id, session_id)
        except ValueError as exc:
            logger.info(f"WhatsApp: action '{action_id}' rejected for session {session_id}: {exc}")
            state = self._chat_service.get_state_for_session(session_id)
            return REPLY_INVALID_ACTION, state["manual_actions"], None, None
        except ServiceError as exc:
            if exc.code in ("session_channel_mismatch", "session_superseded"):
                return REPLY_SESSION_TAKEN_OVER, None, None, None
            if exc.code == "turn_in_progress":
                return REPLY_BUSY, None, None, None
            if exc.code in ("session_closed", "session_not_found"):
                return None, None, exc.code, None
            logger.exception(f"WhatsApp: action '{action_id}' failed for session {session_id}: {exc.message}")
            return "There was a problem processing your message. Please try again.", None, None, None
        return None, None, None, result

    async def _run_action(self, action_id: str) -> tuple[list[Reply], list[dict] | None, int | None]:
        session_id, early = await self._bootstrap_exclusive_session()
        if early is not None:
            return early

        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        notice, manual_actions, retry_code, result = await self._attempt_action(action_id, session_id)
        if retry_code is not None:
            session_id, early = await self._bootstrap_exclusive_session()
            if early is not None:
                return early
            last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
            notice, manual_actions, retry_code, result = await self._attempt_action(action_id, session_id)
            if retry_code is not None:
                notice = REPLY_TECHNICAL_PROBLEM

        if result is None:
            replies = self._new_assistant_replies(session_id, last_seen_id, notice)
            return replies, manual_actions, session_id

        logger.info(f"WhatsApp: action '{action_id}' applied for session {session_id}.")
        state = result["state"]
        replies = self._new_assistant_replies(session_id, last_seen_id)
        if not replies:
            replies = [Reply(state["ui_label"] or REPLY_DONE)]
        return replies, state["manual_actions"], session_id

    def _new_assistant_replies(self, session_id: int, last_seen_id: int, notice: str | None = None) -> list[Reply]:
        replies = [
            Reply(text=to_whatsapp_markdown(m["content"]), audio_text=(m.get("audio_text") or None))
            for m in self._db.get_messages(session_id)
            if m["id"] > last_seen_id and m["role"] == "assistant" and (m["content"] or "").strip()
        ]
        if notice:
            replies.append(Reply(notice))
        return replies

    # ----------------------------------------------------------------- #
    # Outbound delivery — plain text, or the last message as buttons/list
    # ----------------------------------------------------------------- #
    async def send_message(self, phone_number: str, message_md: str, project_id: str) -> bool:
        normalized = phone_number.strip().lstrip("+")
        user = self._db.get_user_by_whatsapp_phone_number(normalized)
        if user is None:
            logger.info(f"WhatsApp: actuator.whatsapp to unregistered number {phone_number} — not sent.")
            return False
        try:
            await self._client.send_text(normalized, to_whatsapp_markdown(message_md))
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp: actuator.whatsapp to {phone_number} failed: {exc}")
            return False
        try:
            await self._chat_service.record_whatsapp_send(user["id"], project_id, message_md)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"WhatsApp: actuator.whatsapp sent to {phone_number} but session logging failed: {exc}")
        return True

    def _wants_voice(self, spoken: bool) -> bool:
        if self._talk_service is None:
            return False
        policy = self._config.voice_replies
        return policy == "always" or (policy == "when-spoken-to" and spoken)

    async def _send_replies(
        self, to: str, replies: list[Reply], manual_actions: list[dict] | None, session_id: int | None,
        voice: bool = False,
    ) -> None:
        """Each reply goes out once — as a voice note when `voice` and the
        reply has an audio text (and the note actually gets sent), as text
        otherwise. Buttons ride on the last reply's text; after a spoken
        last reply they come on a short follow-up prompt instead."""
        replies = [r for r in replies if r.text]
        if not manual_actions:
            for reply in replies:
                await self._send_one(to, reply, voice)
            return
        *leading, last = replies or [Reply(REPLY_DONE)]
        for reply in leading:
            await self._send_one(to, reply, voice)
        if voice and await self._try_voice_note(to, last):
            await self._send_with_buttons(to, REPLY_OPTIONS_PROMPT, manual_actions, session_id)
        else:
            await self._send_with_buttons(to, last.text, manual_actions, session_id)

    async def _send_one(self, to: str, reply: Reply, voice: bool) -> None:
        if voice and await self._try_voice_note(to, reply):
            return
        await self._client.send_text(to, reply.text)

    async def _try_voice_note(self, to: str, reply: Reply) -> bool:
        """True once a voice note for `reply` is on its way; False (with
        the reason logged) whenever it can't be — the caller falls back to
        text, never to silence."""
        logger.info(
            f"WhatsApp: _try_voice_note for {to}: audio_text={reply.audio_text!r} "
            f"talk_service_configured={self._voice_notes is not None}"
        )
        if not reply.audio_text or self._voice_notes is None:
            return False
        from whatsapp.audio import WHATSAPP_AUDIO_MIME

        try:
            mp3 = await self._voice_notes.mp3_for(reply.audio_text)
            if not mp3:
                logger.warning("WhatsApp: talk-service produced no audio, falling back to text.")
                return False
            media_id = await self._client.upload_media(mp3, WHATSAPP_AUDIO_MIME)
            await self._client.send_audio(to, media_id)
            logger.info(f"WhatsApp: audio message sent to {to} ({len(mp3)} bytes, media {media_id}).")
            return True
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad: PyAV's own encoding failures (a partial/
            # truncated WAV from a TalkService generation that ended early)
            # raise its own exception types, not ValueError/httpx.HTTPError —
            # letting one of those escape here would propagate past
            # _send_replies' caller and leave the user with no reply at all
            # instead of the text fallback this docstring promises.
            logger.warning(f"WhatsApp: voice note not sent ({exc}), falling back to text.")
            return False

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


def _notice(text: str) -> tuple[list[Reply], None, None, bool]:
    return [Reply(text)], None, None, False


class VoiceNoteSynthesizer(object):
    """Turns a reply's [audio] text into the MP3 bytes of an audio
    message, and starts doing so the moment the model emits that text —
    ahead of the reply's own text, signals and env — so by the time the
    turn is over and the note is actually wanted, it's already encoded
    (or well on its way). The synthesis and the encoding are one pass:
    every WAV piece TalkService yields goes straight into the encoder."""

    _MAX_PENDING = 16

    def __init__(self, talk_service: "TalkService") -> None:
        self._talk_service = talk_service
        self._pending: dict[str, asyncio.Task[bytes]] = {}

    async def on_metadata(self, key: str, value) -> None:
        if key != "audio" or not value or value in self._pending:
            return
        while len(self._pending) >= self._MAX_PENDING:
            self._pending.pop(next(iter(self._pending))).cancel()
        task = asyncio.create_task(self._synthesize(value))
        task.add_done_callback(_log_unretrieved_failure)
        self._pending[value] = task

    async def mp3_for(self, text: str) -> bytes:
        started = self._pending.pop(text, None)
        if started is not None:
            return await started
        return await self._synthesize(text)

    def cancel(self) -> None:
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()

    async def _synthesize(self, text: str) -> bytes:
        from whatsapp.audio import Mp3Encoder

        encoder = Mp3Encoder()
        async for wav_piece in self._talk_service.generate(text):
            await asyncio.to_thread(encoder.push, wav_piece)
        return await asyncio.to_thread(encoder.finish)


def _log_unretrieved_failure(task: "asyncio.Task[bytes]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"WhatsApp: voice note synthesis started ahead of the reply failed: {exc}")


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
