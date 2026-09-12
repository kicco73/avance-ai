from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import struct

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_middleware import AuthMiddleware
from automaton.project_services import ProjectServices
from system import bus
from system.bus import INPUT_TEXT, OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, POINT_SPOKEN_REPLY, Message
from tracking.spoken_reply import SpokenReply
from turn.input_listener import TurnInput
from whatsapp.config import WhatsAppServiceConfig
from whatsapp.whatsapp_controller import WhatsAppController
from system.service_error import ServiceError
from system.web_session import WebSession
from listen.decoder import SpeechDecoder
from listen.listen_service import ListenServiceError
from talk.audio_stream import AudioStream
from system.audio_format import PcmWavCodec
from whatsapp.audio import split_wav
from whatsapp.whatsapp_service import WhatsAppService


APP_SECRET = "app-secret"
#: Seconds the double stays inside process_turn after announcing the
#: reply's [audio] text, standing in for the generation a real turn spends
#: there.
GENERATING_THE_REST_OF_THE_REPLY = 0.05
LINKED_NUMBER = "34600000001"
LINKED_EMAIL = "alice@example.com"


class _FakeCloudApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.interactive: list[tuple] = []
        self.read: list[str] = []
        self.uploaded: list[tuple[bytes, str]] = []
        self.audio_sent: list[tuple[str, str]] = []
        self.media: dict[str, tuple[bytes, str]] = {"media-in-1": (b"OggS-fake-opus", "audio/ogg; codecs=opus")}
        self.fail_upload = False
        # Every outbound call in order, to assert voice-vs-text-vs-buttons sequencing.
        self.timeline: list[str] = []

    async def send_text(self, to, body):
        self.sent.append((to, body))
        self.timeline.append("text")

    async def send_audio(self, to, media_id):
        self.audio_sent.append((to, media_id))
        self.timeline.append("audio")

    async def upload_media(self, data, mime_type, filename="audio.mp3"):
        if self.fail_upload:
            raise httpx.HTTPError("upload failed")
        self.uploaded.append((data, mime_type))
        return f"media-{len(self.uploaded)}"

    async def download_media(self, media_id):
        if media_id not in self.media:
            raise httpx.HTTPError("no such media")
        return self.media[media_id]

    async def send_buttons(self, to, body, buttons):
        self.interactive.append(("button", to, body, buttons))
        self.timeline.append("buttons")

    async def send_list(self, to, body, button_text, rows):
        self.interactive.append(("list", to, body, button_text, rows))
        self.timeline.append("list")

    async def mark_read_and_show_typing(self, message_id):
        self.read.append(message_id)
        self.timeline.append("typing")

    async def close(self):
        pass


class _FakeDb:
    def __init__(self) -> None:
        self.users = {LINKED_NUMBER: {"id": LINKED_EMAIL, "email": LINKED_EMAIL, "role": "user"}}
        self.messages: list[dict] = []

    def get_user_by_whatsapp_phone_number(self, whatsapp_phone_number):
        return self.users.get(whatsapp_phone_number)

    def get_user_by_id(self, user_id):
        return next((user for user in self.users.values() if user["id"] == user_id), None)

    def get_messages(self, session_id, last_n=None):
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-last_n:] if last_n else rows

    def add(self, session_id, role, content, audio_text=None):
        self.messages.append({
            "id": len(self.messages) + 1, "session_id": session_id, "role": role, "content": content,
            "audio_text": audio_text,
        })
        return self.messages[-1]["id"]

    def row(self, message_id):
        return next(m for m in self.messages if m["id"] == message_id)


class _FakeAuthService:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.valid_codes: dict[str, str] = {}
        self.unexpected_error: Exception | None = None

    def register_via_whatsapp(self, phone_number, invite_code):
        if self.unexpected_error is not None:
            raise self.unexpected_error
        project_name = self.valid_codes.get(invite_code)
        if project_name is None:
            raise PermissionError("This invite link is invalid.")
        self._db.users[phone_number] = {"id": phone_number, "email": None, "role": "user"}
        return project_name


class _FakeChatService:
    """Records who it was called as (WebSession().user) and lets a test
    script the session bootstrap payload, the current state's own
    actions/manual_actions, and the turn/action outcome."""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.session_payload: dict = {"id": 7}
        # What acquire_exclusive_session returns once accept_legal_terms
        # has been called — the resolved shape a real TurnService would
        # reach once the pending gate no longer applies.
        self.resolved_session_payload: dict = {"id": 7}
        self.turn_error: ServiceError | None = None
        self.action_error: Exception | None = None
        # When True, the first raise of turn_error/action_error clears it,
        # so a retried call (session_closed/session_not_found) succeeds —
        # simulates the fresh session a real retry would actually get.
        self.turn_error_clears_after_raise = False
        self.action_error_clears_after_raise = False
        self.opening_message: str | None = None
        self.wrap_up_message: str | None = None
        self.calls: list[tuple] = []
        self.state: dict = {"key": "x", "ui_label": "X", "actions": [], "manual_actions": []}
        self.action_reply_message: str | None = None
        self.reply_audio_text: str | None = None
        self.terms_content: str = "Please accept to continue."
        self.accepted_terms_for: list[str] = []
        self.in_turn = False
        # When True, process_turn persists the user message but reports no
        # reply of its own: a turn already in flight took this message
        # along with its own fragments and answered for both, so what it
        # reports is *that* turn's message — the same row, reported again
        # (see TurnService._already_answered_response).
        self.turn_already_answered = False
        self.answered_by_message_id: int | None = None
        self.announces_audio = True
        self.announced_audio_text: str | None = None

    async def acquire_exclusive_session(self):
        self.calls.append(("session", WebSession().user))
        return self.session_payload

    def get_legal_terms_status(self, project_name):
        self.calls.append(("terms_status", project_name))
        return {"pending": True, "content": self.terms_content}

    def accept_legal_terms(self, project_name):
        self.calls.append(("accept_terms", project_name))
        self.accepted_terms_for.append(project_name)
        self.session_payload = self.resolved_session_payload

    async def get_messages(self, session_id):
        if self.opening_message and not self.db.get_messages(session_id):
            self.db.add(session_id, "assistant", self.opening_message)
        return self.db.get_messages(session_id)

    async def prepare_user_initiated_turn(self, session_id):
        """Returns what it persisted, like the real one: the wrap-up is
        not in the turn's own reply, so a caller that reports the turn
        would otherwise never hear about it."""
        if self.wrap_up_message and not self.db.get_messages(session_id):
            return [self.db.row(self.db.add(session_id, "assistant", self.wrap_up_message))]
        return []

    def get_state_for_session(self, session_id):
        return self.state

    def accept_user_message(self, session_id, text):
        """Persisted before the turn runs and handed over as an id, like
        the real one — a turn that never happens still leaves the message
        the person sent."""
        return self.db.add(session_id, "user", text)

    def spoken_reply_wanted(self, session_id):
        """The real question core asks before building the prompt (see
        tracking/spoken_reply.py): nobody wanting it, or nothing able to
        speak, and the reply is never given an [audio] text at all."""
        return bus.collect(
            POINT_SPOKEN_REPLY, SpokenReply(services=ProjectServices({}), session_id=session_id),
        ).asked

    async def process_turn(self, session_id, text, on_metadata=None, user_message_id=None):
        self.calls.append(("turn", WebSession().user))
        if self.turn_error is not None:
            error = self.turn_error
            if self.turn_error_clears_after_raise:
                self.turn_error = None
            raise error
        self.in_turn = True
        try:
            # The real turn emits the reply's [audio] text well before the
            # rest of the reply is written, and then spends seconds writing
            # it — long enough for the synthesis that announcement starts
            # to get going. On the Bus that is a queued frame, a drain task
            # and a listener away, so the double has to stay in the turn
            # for more than the single loop tick it used to.
            audio_text = self.reply_audio_text if self.spoken_reply_wanted(session_id) else None
            if on_metadata is not None and self.announces_audio and audio_text:
                on_metadata("audio", self.announced_audio_text or audio_text)
                await asyncio.sleep(GENERATING_THE_REST_OF_THE_REPLY)
            if user_message_id is None:
                self.db.add(session_id, "user", text)
            if self.turn_already_answered:
                answered_by = self.answered_by_message_id
                return {
                    "session_id": session_id, "state": self.state,
                    "assistant_message_id": answered_by,
                    "reply": [self.db.row(answered_by)] if answered_by else [],
                }
            assistant_id = self.db.add(
                session_id, "assistant", f"**Hola** — has dicho: {text}", audio_text=audio_text,
            )
        finally:
            self.in_turn = False
        # `reply` is the turn's own assistant message, exactly one, same
        # as TrackingProcessor._build_turn_response — and never the
        # wrap-up that prepare_user_initiated_turn wrote.
        return {
            "session_id": session_id, "state": self.state, "assistant_message_id": assistant_id,
            "reply": [self.db.row(assistant_id)],
        }

    async def apply_manual_action(self, action_name, session_id):
        self.calls.append(("action", WebSession().user, action_name))
        if self.action_error is not None:
            error = self.action_error
            if self.action_error_clears_after_raise:
                self.action_error = None
            raise error
        reply = []
        if self.action_reply_message:
            reply = [self.db.row(self.db.add(session_id, "assistant", self.action_reply_message))]
        return {"session_id": session_id, "state": self.state, "reply": reply}


def _wav(seconds: float = 0.5, rate: int = 22050) -> bytes:
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(rate * seconds)))
    return PcmWavCodec.to_wav(pcm, rate)


class _FakeTalk:
    """TalkService stand-in: streams the WAV the way the real one does
    (streaming header first, then PCM chunks)."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.silent = False
        self.chat: _FakeChatService | None = None
        self.requested_during_turn: list[bool] = []

    async def generate(self, text):
        self.spoken.append(text)
        self.requested_during_turn.append(self.chat.in_turn if self.chat is not None else False)
        if self.silent:
            return
        pcm, rate = split_wav(_wav())
        yield PcmWavCodec.streaming_header(rate)
        for i in range(0, len(pcm), 4096):
            yield pcm[i:i + 4096]


class _FakeListen:
    def __init__(self, transcript: str = "hola por voz") -> None:
        self.transcript = transcript
        self.heard: list[bytes] = []
        self.fail = False

    async def transcribe(self, audio):
        self.heard.append(audio)
        if self.fail:
            raise ListenServiceError("whisper down")
        return self.transcript


def _config(**overrides) -> WhatsAppServiceConfig:
    values = dict(
        verify_token="my-verify-token", app_secret=APP_SECRET, access_token="tok", phone_number_id="123",
        phone_number="15552052260", invite_prefix="Invitation code: ", graph_version="v23.0", mark_read=True,
        voice_replies="when-spoken-to",
    )
    values.update(overrides)
    return WhatsAppServiceConfig(**values)


def _payload(msg_id="wamid.1", sender=LINKED_NUMBER, text="hola", mtype="text") -> dict:
    message = {"from": sender, "id": msg_id, "timestamp": "1749416383", "type": mtype}
    if mtype == "text":
        message["text"] = {"body": text}
    elif mtype == "audio":
        # Real shape of an inbound voice note: no bytes, just a media id to download.
        message["audio"] = {"id": "media-in-1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34900000000", "phone_number_id": "123"},
        "contacts": [{"profile": {"name": "Alice"}, "wa_id": sender}],
        "messages": [message],
    }}]}]}


# Real Meta webhook shapes for a tapped reply button and a tapped list
# row — _interactive_payload below builds the same `messages[0]` shape
# generically, `kind`/`reply` matching the two "interactive" sub-objects
# actually seen on the wire:
#
# button_reply: {"type": "interactive", "interactive": {
#     "type": "button_reply", "button_reply": {"id": "go", "title": "Go"},
# }}
# list_reply: {"type": "interactive", "interactive": {
#     "type": "list_reply", "list_reply": {"id": "opt2", "title": "Option 2", "description": "..."},
# }}
def _interactive_payload(msg_id="wamid.1", sender=LINKED_NUMBER, kind="button_reply", reply=None) -> dict:
    reply = reply or {"id": "go", "title": "Go"}
    message = {
        "from": sender, "id": msg_id, "timestamp": "1749416383", "type": "interactive",
        "interactive": {"type": kind, kind: reply},
    }
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34900000000", "phone_number_id": "123"},
        "contacts": [{"profile": {"name": "Alice"}, "wa_id": sender}],
        "messages": [message],
    }}]}]}


def _action(name, ui_button, ui_description=None, has_trigger=False) -> dict:
    return {
        "name": name, "ui_label": name, "ui_button": ui_button, "ui_description": ui_description,
        "target": "y", "has_trigger": has_trigger, "task": None,
    }


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build(config=None, talk=None, listen=None):
    db = _FakeDb()
    chat = _FakeChatService(db)
    api = _FakeCloudApi()
    auth = _FakeAuthService(db)
    if listen is not None:
        # Listen reaches this channel through the Bus now, never as a
        # constructor argument: the service does not know it exists.
        SpeechDecoder(listen).register()
    # Stands in for the talk skill: registered for output.speech, it
    # answers with output.audio on the same envelope. One listener per
    # _build, so a second call in the same test never answers the first's.
    bus._listeners[OUTPUT_SPEECH] = []
    if talk is not None:
        async def speak(message: Message) -> None:
            await bus.publish(message.converted(
                OUTPUT_AUDIO_STREAM, {"stream": AudioStream(talk, str(message.body["text"]))}, mime="audio/wav",
            ))

        bus.subscribe(OUTPUT_SPEECH, speak)
        # The other half of what the talk skill registers: a build that
        # can speak says so where the prompt is built (see talk/skill.py's
        # own POINT_SPOKEN_REPLY contributor), and a turn is only ever
        # given an [audio] text when something answered there.
        bus.contribute(POINT_SPOKEN_REPLY, lambda spoken: spoken.ask())
    service_config = config or _config()
    service = WhatsAppService(service_config, chat, db, auth, client=api)
    service.register()
    # Turns are run by core, off the Bus, exactly as they are in the real
    # process: this channel posts `input.text` and reads the frames that
    # come back (see turn/input_listener.py). One listener per _build, like
    # output.speech below: a second env in the same test must not have the
    # first one's turn service answer for it.
    bus._listeners[INPUT_TEXT] = []
    TurnInput(chat, db).register()
    app = FastAPI()
    # The real app's login wall sits in front of these routes too — they
    # must be reachable with no cookie at all (role=None).
    app.add_middleware(AuthMiddleware)
    from fastapi import APIRouter
    router = APIRouter()
    WhatsAppController(service, service_config).register_routes(router)
    app.include_router(router)
    return TestClient(app), service, chat, db, api


@pytest.fixture
def env():
    return _build()


@pytest.fixture
def voice_env():
    """Both voice services on, default policy (answer in kind)."""
    talk, listen = _FakeTalk(), _FakeListen()
    client, service, chat, db, api = _build(talk=talk, listen=listen)
    talk.chat = chat
    return client, service, chat, db, api, talk, listen


def _post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/skills/whatsapp/webhook", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature if signature is not None else _sign(body)},
    )
