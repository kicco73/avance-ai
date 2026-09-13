"""WhatsApp as an alternative chat client (see docs/WHATSAPP.md).

A router, not a turn engine: an inbound text from a linked number is
posted on the Bus as `input.text` for that account's own current live
session (its active project, its sessions, its Terms acceptance —
nothing WhatsApp-specific is persisted), and what comes back on the Bus
goes out through the Cloud API. Which session, and what a refusal sounds
like here, is all this channel decides for itself — see
whatsapp/turn_exchange.py and docs/BUS.md.

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
(WebSession().impersonate) for the duration of the turn — the same
ContextVar-backed WebSession() every service reads the current user from.
An unlinked number whose message doesn't resolve to a valid invite code
either, or an unregistered one, only ever gets a canned reply.

Ordering: Meta may deliver two messages from the same person back to
back; a per-sender asyncio.Lock keeps their turns sequential (on top of
TurnService's own per-session lock, which would otherwise just make the
second one wait in whichever order the event loop picked).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from auth.auth_service import AuthService
from turn.turn_service import TurnService
from whatsapp.config import WhatsAppServiceConfig
from db import Db
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from system import bus
from system.bus import OUTPUT_TEXT, Message
from system.web_session import WebSession
from talker import AiTalker
from whatsapp.cloud_api_client import WhatsAppCloudApiClient
from whatsapp.outbound import REPLY_DONE, Outbound, Reply, replies_from
from whatsapp.inbound_voice_note import InboundVoiceNote
from whatsapp.turn_exchange import TextReply, TurnExchange, TurnOutcome, VoiceReply
from whatsapp.voice_notes import VoiceNoteSynthesizer
from whatsapp.webhook import IncomingMessage, to_whatsapp_markdown

logger = LoggerFactory.get_logger(__name__)

#: The channel this package is. Not a string that happens to match the
#: directory — the skill key is derived from the package name too (see
#: skills.Skill.__init_subclass__), so the name is written down once, and
#: it is the directory.
CHANNEL = __package__


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
REPLY_NO_CHAT_STATE = "The conversation doesn't accept messages at this point. Continue from the web."
REPLY_REGISTERED = "You're all set! Registration complete — you can start chatting now."
REPLY_INVALID_ACTION = "That option is no longer available. Please choose one of these instead."
REPLY_BUSY = "Please wait a moment and try again."
REPLY_SESSION_TAKEN_OVER = "This conversation continued somewhere else. Send another message to keep chatting here."
REPLY_TECHNICAL_PROBLEM = "We apologize for the inconvenience — a technical problem occurred. Please try again in a moment."
REPLY_ACCEPT_TERMS_LABEL = "Accept"
REPLY_TURN_PROBLEM = "There was a problem processing your message. Please try again."

# Reserved action id for the terms-acceptance button — distinct from any
# real automaton action name, dispatched before _run_action ever sees it.
_ACCEPT_TERMS_ACTION = "__whatsapp_accept_terms__"


@dataclass(frozen=True)
class _Notice:
    text: str
    keeps_actions: bool = False


_NOTICES = {
    "state_not_chat": _Notice(REPLY_NO_CHAT_STATE, keeps_actions=True),
    "session_channel_mismatch": _Notice(REPLY_SESSION_TAKEN_OVER),
    "session_superseded": _Notice(REPLY_SESSION_TAKEN_OVER),
    "turn_in_progress": _Notice(REPLY_BUSY),
    "project_unavailable": _Notice(REPLY_PAUSED),
    "session_closed": _Notice(REPLY_TECHNICAL_PROBLEM),
    "session_not_found": _Notice(REPLY_TECHNICAL_PROBLEM),
}
_NOTICE_UNEXPECTED = _Notice(REPLY_TURN_PROBLEM)

# A session that closed or vanished under a message is recovered from in
# silence: on WhatsApp nobody ever saw it, which is this channel's own
# policy and the reason the retry is here and not in core.
_RETRIED_CODES = ("session_closed", "session_not_found")


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
        self._auth_service = auth_service
        self._assistant_talker = AiTalker()
        self._client = client or WhatsAppCloudApiClient(
            config.access_token, config.phone_number_id, config.graph_version
        )
        self._sender_locks: dict[str, asyncio.Lock] = {}
        self._voice_notes = VoiceNoteSynthesizer(self._assistant_talker)
        self._voice_notes_in = InboundVoiceNote(self._client)
        # Every limit WhatsApp puts on a message, and the voice-note
        # upload, live there rather than here (see whatsapp/outbound.py).
        self._outbound = Outbound(self._client, self._voice_notes)

    def register(self) -> None:
        """What reaches this channel from anywhere else: a text addressed
        to a WhatsApp number. task.whatsapp() posts one and reads the
        posting's own answer — nothing on the other side names this
        package."""
        bus.subscribe(OUTPUT_TEXT, self._send_outbound)

    async def _send_outbound(self, message: Message) -> None:
        for _ in filter(CHANNEL.__eq__, [message.channel]):
            body = message.body if isinstance(message.body, dict) else {"text": message.body}
            await self.send_message(str(message.username), str(body.get("text") or ""), str(message.project_id))

    async def close(self) -> None:
        bus.unsubscribe(OUTPUT_TEXT, self._send_outbound)
        self._voice_notes.cancel()
        await self._client.close()

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
                    replies, buttons, session_id, spoken = await self._replies_for(message)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"WhatsApp: unexpected error resolving a reply to {message.id} from {message.sender}: {exc}")
                    await self._client.send_text(message.sender, REPLY_TECHNICAL_PROBLEM)
                    return
                await self._outbound.send(
                    message.sender, replies, buttons, session_id, voice=self._wants_voice(spoken),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"WhatsApp: unhandled error on message {message.id} from {message.sender}: {exc}")

    async def _replies_for(self, message: IncomingMessage) -> tuple[list[Reply], list[dict] | None, int | None, bool]:
        """(replies, buttons, session_id, spoken) — `spoken` is
        True when the user's message came in as a voice note, which is
        what the voice-replies policy keys on."""
        user = self._db.get_user_by_whatsapp_phone_number(message.sender)
        if user is None:
            return (*await self._handle_unlinked(message), False)
        if user.get("role") in (None, "pending"):
            return _notice(REPLY_NOT_REGISTERED)

        with WebSession().impersonate(user["id"]):
            WebSession().role = user["role"]
            WebSession().channel = CHANNEL
            if message.type == "interactive" and message.action_id:
                logger.info(f"WhatsApp: action '{message.action_id}' received ({message.id}) from {message.sender}.")
                if message.action_id == _ACCEPT_TERMS_ACTION:
                    return (*await self._accept_terms_action(), False)
                return (*await self._run_action(message.action_id), False)
            if message.type == "audio" and message.audio_id:
                text = await self._voice_notes_in.decoded_text(message, message.audio_id)
                if text is None:
                    return _notice(self._voice_notes_in.notice())
                return (*await self._run_turn(message, text, spoken=True), True)
            text = (message.text or "").strip()
            if message.type != "text" or not text:
                return _notice(REPLY_UNSUPPORTED)
            return (*await self._run_turn(message, text), False)

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
        with WebSession().impersonate(user["id"]):
            WebSession().role = user["role"]
            WebSession().channel = CHANNEL
            welcome_texts, buttons, session_id = await self._welcome_replies()
            return [Reply(REPLY_REGISTERED), *welcome_texts], buttons, session_id

    async def _welcome_replies(self) -> tuple[list[Reply], list[dict] | None, int | None]:
        """Right after a brand-new WhatsApp registration: same session
        bootstrap a real turn starts with, minus process_turn — there's no
        user message to run, just whatever opening message the project
        itself produces (if any), same as a fresh web registration seeing
        it immediately on landing rather than only after its first reply."""
        session_payload = await self._turn_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return [], None, None
        if session_payload.get("legal_terms_pending"):
            return self._terms_reply(session_payload["project_id"])
        session_id = session_payload["id"]
        replies, buttons = await self._bootstrap_replies(session_id)
        return replies, buttons, session_id

    def _terms_reply(self, project_id: str) -> tuple[list[Reply], list[dict] | None, int | None]:
        """The project's own legal/terms.md plus an Accept button — the
        WhatsApp equivalent of TermsView.vue, in place of the plain "go
        accept it on the web" notice this used to send."""
        status = self._turn_service.get_legal_terms_status(project_id)
        buttons = [{"name": _ACCEPT_TERMS_ACTION, "ui_button": REPLY_ACCEPT_TERMS_LABEL, "ui_description": None}]
        return [Reply(to_whatsapp_markdown(status["content"] or ""))], buttons, None

    async def _accept_terms_action(self) -> tuple[list[Reply], list[dict] | None, int | None]:
        session_payload = await self._turn_service.acquire_exclusive_session()
        if session_payload.get("legal_terms_pending"):
            self._turn_service.accept_legal_terms(session_payload["project_id"])
            session_payload = await self._turn_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return [Reply(REPLY_PAUSED)], None, None
        if session_payload.get("legal_terms_pending"):
            return self._terms_reply(session_payload["project_id"])
        session_id = session_payload["id"]
        replies, buttons = await self._bootstrap_replies(session_id)
        return replies or [Reply(REPLY_TERMS_ACCEPTED)], buttons, session_id

    async def _bootstrap_replies(self, session_id: int) -> tuple[list[Reply], list[dict] | None]:
        """New assistant content, if any, once a session is confirmed
        resolvable — a fresh session's own opening message, or nothing for
        one whose history was already delivered earlier (e.g. right after
        accepting terms mid-conversation, where the baseline is whatever
        the session already had before this call)."""
        last_seen_id = max((m["id"] for m in self._db.get_messages(session_id, last_n=1)), default=0)
        for _ in filter(None, [not last_seen_id]):
            await self._turn_service.open_conversation(session_id)
        messages = self._turn_service.read_history(session_id)
        fresh = [m for m in messages if m["id"] > last_seen_id and m["role"] == "assistant"]
        return replies_from(fresh), self._buttons_of(session_id)

    async def _bootstrap_exclusive_session(self) -> tuple[dict | None, tuple[list[Reply], list[dict] | None, int | None] | None]:
        """(session, None) once resolved, or (None, early_result) for the
        caller to return as-is (paused/terms-pending) without ever
        reaching a session at all."""
        session_payload = await self._turn_service.acquire_exclusive_session()
        if session_payload.get("paused"):
            return None, ([Reply(REPLY_PAUSED)], None, None)
        if session_payload.get("legal_terms_pending"):
            return None, self._terms_reply(session_payload["project_id"])
        return session_payload, None

    async def _run_turn(
        self, message: IncomingMessage, text: str, spoken: bool = False,
    ) -> tuple[list[Reply], list[dict] | None, int | None]:
        """This channel does not run turns: it posts what the person said
        on the Bus and delivers what comes back — see docs/BUS.md."""
        session, early = await self._bootstrap_exclusive_session()
        if early is not None:
            return early

        outcome = await self._exchange(message, session, text, spoken).run()
        if outcome.code in _RETRIED_CODES:
            session, early = await self._bootstrap_exclusive_session()
            if early is not None:
                return early
            outcome = await self._exchange(message, session, text, spoken).run()

        session_id = session["id"]
        notice, buttons = self._notice_for(outcome, session_id)
        return replies_from(outcome.messages, notice), buttons, session_id

    def _exchange(
        self, message: IncomingMessage, session: dict, text: str, spoken: bool,
    ) -> TurnExchange:
        return TurnExchange(
            channel=CHANNEL, username=WebSession().user, session_id=session["id"], text=text,
            origin_id=message.id, project_id=session.get("project_id"),
            voice=self._voice_for(spoken), db=self._db,
        )

    def _voice_for(self, spoken: bool) -> TextReply | VoiceReply:
        return {
            True: VoiceReply(self._voice_notes), False: TextReply(),
        }[self._wants_voice(spoken)]

    def _notice_for(self, outcome: TurnOutcome, session_id: int) -> tuple[str | None, list[dict] | None]:
        for code in filter(None, [outcome.code]):
            notice = _NOTICES.get(code, _NOTICE_UNEXPECTED)
            return notice.text, self._buttons_after(notice, session_id)
        return None, outcome.buttons

    def _buttons_after(self, notice: "_Notice", session_id: int) -> list[dict] | None:
        for _ in filter(None, [notice.keeps_actions]):
            return self._buttons_of(session_id)
        return None

    def _buttons_of(self, session_id: int) -> list[dict]:
        """What the session offers right now. Read as its own thing: the
        state payload does not carry the choices any more (see
        TurnService.buttons_for)."""
        return self._turn_service.buttons_for(
            session_id, self._turn_service.get_state_for_session(session_id),
        )

    async def _attempt_action(
        self, action_id: str, session_id: int,
    ) -> tuple[str | None, list[dict] | None, str | None, dict | None]:
        """(notice, buttons, retry_code, result) for one manual
        action attempt — `result` is apply_manual_action's own return
        value, set only on success."""
        try:
            result = await self._turn_service.apply_manual_action(action_id, session_id)
        except ValueError as exc:
            logger.info(f"WhatsApp: action '{action_id}' rejected for session {session_id}: {exc}")
            return REPLY_INVALID_ACTION, self._buttons_of(session_id), None, None
        except ServiceError as exc:
            if exc.code in ("session_channel_mismatch", "session_superseded"):
                return REPLY_SESSION_TAKEN_OVER, None, None, None
            if exc.code == "turn_in_progress":
                return REPLY_BUSY, None, None, None
            if exc.code in _RETRIED_CODES:
                return None, None, exc.code, None
            logger.exception(f"WhatsApp: action '{action_id}' failed for session {session_id}: {exc.message}")
            return REPLY_TURN_PROBLEM, None, None, None
        return None, None, None, result

    async def _run_action(self, action_id: str) -> tuple[list[Reply], list[dict] | None, int | None]:
        session, early = await self._bootstrap_exclusive_session()
        if early is not None:
            return early

        session_id = session["id"]
        notice, buttons, retry_code, result = await self._attempt_action(action_id, session_id)
        if retry_code is not None:
            session, early = await self._bootstrap_exclusive_session()
            if early is not None:
                return early
            session_id = session["id"]
            notice, buttons, retry_code, result = await self._attempt_action(action_id, session_id)
            if retry_code is not None:
                notice = REPLY_TECHNICAL_PROBLEM

        if result is None:
            return replies_from([], notice), buttons, session_id

        logger.info(f"WhatsApp: action '{action_id}' applied for session {session_id}.")
        state = result["state"]
        replies = replies_from(result["reply"])
        if not replies:
            replies = [Reply(state["ui_label"] or REPLY_DONE)]
        return replies, result["buttons"], session_id

    # ----------------------------------------------------------------- #
    # Outbound delivery — plain text, or the last message as buttons/list
    # ----------------------------------------------------------------- #
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
            # The turn service opens a session on whatever channel is
            # current if this user has none; naming it is ours to do.
            WebSession().channel = CHANNEL
            await self._turn_service.record_unsolicited_reply(user["id"], project_id, message_md)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"WhatsApp: task.whatsapp sent to {phone_number} but session logging failed: {exc}")
        return True

    def _wants_voice(self, spoken: bool) -> bool:
        """The project's own voice-replies policy, asked of a build that
        can actually speak — `can_speak` is False when nothing here turns
        text into audio, and then no policy makes it voice."""
        if not self._outbound.can_speak:
            return False
        policy = self._config.voice_replies
        return policy == "always" or (policy == "when-spoken-to" and spoken)

def _notice(text: str) -> tuple[list[Reply], None, None, bool]:
    return [Reply(text)], None, None, False
