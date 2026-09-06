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
from config import WhatsAppServiceConfig
from controllers.whatsapp_controller import WhatsAppController
from service_error import ServiceError
from session import Session
from listen.listen_service import ListenServiceError
from talk.talk_format import PcmWavCodec
from whatsapp.audio import split_wav
from whatsapp.whatsapp_service import WhatsAppService


APP_SECRET = "app-secret"
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

    def get_messages(self, session_id, last_n=None):
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-last_n:] if last_n else rows

    def add(self, session_id, role, content, audio_text=None):
        self.messages.append({
            "id": len(self.messages) + 1, "session_id": session_id, "role": role, "content": content,
            "audio_text": audio_text,
        })


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
    """Records who it was called as (Session().user) and lets a test
    script the session bootstrap payload, the current state's own
    actions/manual_actions, and the turn/action outcome."""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.session_payload: dict = {"id": 7}
        # What acquire_exclusive_session returns once accept_legal_terms
        # has been called — the resolved shape a real ChatService would
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
        self.announces_audio = True
        self.announced_audio_text: str | None = None

    async def acquire_exclusive_session(self):
        self.calls.append(("session", Session().user))
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
        if self.wrap_up_message and not self.db.get_messages(session_id):
            self.db.add(session_id, "assistant", self.wrap_up_message)

    def get_state_for_session(self, session_id):
        return self.state

    async def process_turn(self, session_id, text, on_metadata=None):
        self.calls.append(("turn", Session().user))
        if self.turn_error is not None:
            error = self.turn_error
            if self.turn_error_clears_after_raise:
                self.turn_error = None
            raise error
        self.in_turn = True
        try:
            # The real turn emits the reply's [audio] text well before the
            # rest of the reply is written — mirrored here, with a yield to
            # the loop so whatever that callback started gets to run mid-turn.
            if on_metadata is not None and self.announces_audio and self.reply_audio_text:
                on_metadata("audio", self.announced_audio_text or self.reply_audio_text)
                await asyncio.sleep(0)
            self.db.add(session_id, "user", text)
            self.db.add(session_id, "assistant", f"**Hola** — has dicho: {text}", audio_text=self.reply_audio_text)
        finally:
            self.in_turn = False
        return {"session_id": session_id, "state": self.state}

    async def apply_manual_action(self, action_name, session_id):
        self.calls.append(("action", Session().user, action_name))
        if self.action_error is not None:
            error = self.action_error
            if self.action_error_clears_after_raise:
                self.action_error = None
            raise error
        if self.action_reply_message:
            self.db.add(session_id, "assistant", self.action_reply_message)
        return {"session_id": session_id, "state": self.state}


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
        "target": "y", "has_trigger": has_trigger, "on-enter": None,
    }


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build(config=None, talk=None, listen=None):
    db = _FakeDb()
    chat = _FakeChatService(db)
    api = _FakeCloudApi()
    auth = _FakeAuthService(db)
    service = WhatsAppService(config or _config(), chat, db, auth, client=api, talk_service=talk, listen_service=listen)
    app = FastAPI()
    # The real app's login wall sits in front of these routes too — they
    # must be reachable with no cookie at all (role=None).
    app.add_middleware(AuthMiddleware)
    from fastapi import APIRouter
    router = APIRouter()
    WhatsAppController(service).register_routes(router)
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
        "/api/whatsapp/webhook", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature if signature is not None else _sign(body)},
    )
