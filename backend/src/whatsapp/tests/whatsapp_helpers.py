from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import struct
from typing import Awaitable

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.auth_middleware import AuthMiddleware
from config import REPLY_SILENCE_SECONDS
from automaton.project_services import ProjectServices
from system import bus
from system.audio_format import PcmWavCodec
from system.bus import (
    INPUT_AUDIO, INPUT_TEXT, OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, POINT_SPOKEN_REPLY, Message,
)
from system.service_error import ServiceError
from tracking.spoken_reply import SpokenReply
from turn.input_listener import TurnInput
from whatsapp.audio import split_wav
from whatsapp.config import WhatsAppServiceConfig
from whatsapp.webhook import extract_incoming
from whatsapp.whatsapp_service import WhatsAppService

APP_SECRET = "app-secret"
LINKED_NUMBER = "34600000001"
LINKED_EMAIL = "alice@example.com"
UNKNOWN_NUMBER = "34699999999"
PROJECT = "demo-project"
SESSION_ID = 7
GENERATING_THE_REST_OF_THE_REPLY = 0.05


async def answered(work: Awaitable[None]) -> None:
    """`work`, and everything the Bus started because of it. A channel
    answers Meta's webhook before the turn runs, so what a test is waiting
    for is never finished when the call it made returns."""
    before = set(asyncio.all_tasks())
    await work
    while True:
        pending = [
            task for task in asyncio.all_tasks()
            if task not in before and task is not asyncio.current_task() and not task.done()
        ]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


class _FakeCloudApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.interactive: list[tuple] = []
        self.read: list[str] = []
        self.uploaded: list[tuple[bytes, str]] = []
        self.audio_sent: list[tuple[str, str]] = []
        self.media: dict[str, tuple[bytes, str]] = {"media-in-1": (b"OggS-fake-opus", "audio/ogg; codecs=opus")}
        self.fail_upload = False
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

    @property
    def bodies(self) -> list[str]:
        return [body for _, body in self.sent]


class _FakeDb:
    def __init__(self) -> None:
        self.users = {LINKED_NUMBER: {"id": LINKED_EMAIL, "email": LINKED_EMAIL, "role": "user"}}
        self.messages: list[dict] = []
        self.active_project = PROJECT

    def get_user_by_whatsapp_phone_number(self, whatsapp_phone_number):
        return self.users.get(whatsapp_phone_number)

    def get_user_by_id(self, user_id):
        return next((user for user in self.users.values() if user["id"] == user_id), None)

    def get_active_project_id(self, user):
        return self.active_project

    def get_messages(self, session_id, last_n=None):
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-last_n:] if last_n else rows

    def add(self, session_id, role, content, audio_text=None):
        self.messages.append({
            "id": len(self.messages) + 1, "session_id": session_id, "role": role, "content": content,
            "audio_text": audio_text, "timestamp": None,
        })
        return self.messages[-1]["id"]

    def row(self, message_id):
        return next(m for m in self.messages if m["id"] == message_id)

    def get_message(self, message_id):
        return next((m for m in self.messages if m["id"] == message_id), None)


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
            raise PermissionError("This invite code is unknown.")
        self._db.users[phone_number] = {"id": phone_number, "email": None, "role": "user"}
        return project_name


class _FakeTurns:
    """What core runs turns with, as this channel meets it — reached only
    through `turn/input_listener.py`, which is the real one."""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.state: dict = {"key": "x", "ui_label": "X", "actions": []}
        self.buttons: list[dict] = []
        self.session: dict = {"id": SESSION_ID, "channel": "whatsapp", "project_id": PROJECT}
        self.created: dict = {"id": SESSION_ID + 1, "channel": "whatsapp", "project_id": PROJECT}
        self.calls: list[tuple] = []
        self.turn_error: ServiceError | None = None
        self.action_error: Exception | None = None
        self.action_reply_message: str | None = None
        self.wrap_up_message: str | None = None
        self.reply_audio_text: str | None = None
        self.announces_audio = True
        self.announced_audio_text: str | None = None
        self.terms_content: str = "Please accept to continue."
        self.accepted_terms_for: list[str] = []
        self.in_turn = False
        self.reply_silence_seconds = REPLY_SILENCE_SECONDS

    async def enter_session(self, project_id, type):
        self.calls.append(("enter", project_id, type))
        return self._payload(self.session)

    async def create_session_of(self, project_id, type):
        self.calls.append(("create", project_id, type))
        self.session = self.created
        return self._payload(self.created)

    def _payload(self, session: dict) -> dict:
        return {**session, "state": self.state} if "id" in session else dict(session)

    def read_history(self, session_id, last_n=None):
        return self.db.get_messages(session_id)

    def services_for(self, session_id):
        return {}

    def buttons_for(self, session_id, state_payload):
        return self.buttons

    def choice_options_for(self, session_id):
        return {}

    async def prepare_user_initiated_turn(self, session_id):
        if self.wrap_up_message and not self.db.get_messages(session_id):
            return [self.db.row(self.db.add(session_id, "assistant", self.wrap_up_message))]
        return []

    def accept_user_message(self, session_id, text):
        if self.turn_error is not None:
            raise self.turn_error
        return self.db.add(session_id, "user", text)

    def spoken_reply_wanted(self, session_id):
        return bus.collect(
            POINT_SPOKEN_REPLY, SpokenReply(services=ProjectServices({}), session_id=session_id),
        ).asked

    async def process_turn(self, session_id, text, on_metadata=None, user_messages=None):
        self.calls.append(("turn", session_id, text))
        self.in_turn = True
        try:
            audio_text = self.reply_audio_text if self.spoken_reply_wanted(session_id) else None
            if on_metadata is not None and self.announces_audio and audio_text:
                on_metadata("audio", self.announced_audio_text or audio_text)
                await asyncio.sleep(GENERATING_THE_REST_OF_THE_REPLY)
            assistant_id = self.db.add(
                session_id, "assistant", f"**Hola** — has dicho: {text}", audio_text=audio_text,
            )
        finally:
            self.in_turn = False
        return {
            "session_id": session_id, "state": self.state, "buttons": self.buttons,
            "assistant_message_id": assistant_id, "reply": [self.db.row(assistant_id)],
        }

    async def apply_manual_action(self, action_name, session_id, on_metadata=None):
        self.calls.append(("action", session_id, action_name))
        if self.action_error is not None:
            raise self.action_error
        reply = []
        if self.action_reply_message:
            reply = [self.db.row(self.db.add(session_id, "assistant", self.action_reply_message))]
        return {
            "session_id": session_id, "state": self.state, "state_changed": True,
            "new_state": self.state["key"], "triggered_action": action_name,
            "buttons": self.buttons, "reply": reply,
        }

    async def record_unsolicited_reply(self, username, project_id, content):
        self.calls.append(("unsolicited", username, project_id))
        self.db.add(SESSION_ID, "assistant", content)

    def get_legal_terms_status(self, project_id):
        self.calls.append(("terms_status", project_id))
        return {"pending": True, "content": self.terms_content}

    def accept_legal_terms(self, project_id):
        self.calls.append(("accept_terms", project_id))
        self.accepted_terms_for.append(project_id)
        self.session = {"id": SESSION_ID, "channel": "whatsapp", "project_id": PROJECT}


def _wav(seconds: float = 0.5, rate: int = 22050) -> bytes:
    pcm = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds))
    )
    return PcmWavCodec.to_wav(pcm, rate)


class _SpokenText:

    def __init__(self, speaker: "_FakeSpeaker", text: str) -> None:
        self._speaker = speaker
        self._text = text

    def chunks(self):
        return self._speaker.generate(self._text)


class _FakeSpeaker:
    """Whoever speaks in this build, as this channel meets it: one
    listener for `output.speech` answering with `output.audio_stream`."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.silent = False
        self.turns: _FakeTurns | None = None
        self.requested_during_turn: list[bool] = []

    def register(self) -> None:
        bus.subscribe(OUTPUT_SPEECH, self._speak)

    async def _speak(self, message: Message) -> None:
        await bus.publish(message.converted(
            OUTPUT_AUDIO_STREAM, {"stream": _SpokenText(self, str(message.body["text"]))}, mime="audio/wav",
        ))

    async def generate(self, text):
        self.spoken.append(text)
        self.requested_during_turn.append(self.turns.in_turn if self.turns is not None else False)
        if self.silent:
            return
        pcm, rate = split_wav(_wav())
        yield PcmWavCodec.streaming_header(rate)
        for i in range(0, len(pcm), 4096):
            yield pcm[i:i + 4096]


class _FakeDecoder:
    """Whoever decodes speech in this build: one listener for
    `input.audio` answering with `input.text` on the same envelope."""

    def __init__(self, transcript: str = "hola por voz") -> None:
        self.transcript = transcript
        self.heard: list[bytes] = []

    def register(self) -> None:
        bus.subscribe(INPUT_AUDIO, self._decode)

    async def _decode(self, message: Message) -> None:
        source = (message.body or {}).get("audio")
        self.heard.append(await source() if callable(source) else source)
        await self._answer(message)

    async def _answer(self, message: Message) -> None:
        await bus.publish(message.converted(INPUT_TEXT, {"text": self.transcript}))


class _SpeechlessDecoder(_FakeDecoder):
    """Registered for the same audio and making no words of it."""

    async def _answer(self, message: Message) -> None:
        return


def _config(**overrides) -> WhatsAppServiceConfig:
    values = dict(
        verify_token="my-verify-token", app_secret=APP_SECRET, access_token="tok", phone_number_id="123",
        phone_number="15552052260", invite_prefix="Invitation code: ", graph_version="v23.0", mark_read=True,
        voice_replies="when-spoken-to",
    )
    values.update(overrides)
    return WhatsAppServiceConfig(**values)


class Env:
    """One channel, wired the way the skill wires it: the service, the
    Cloud API it speaks through, and the real `TurnInput` behind the Bus."""

    def __init__(self, config=None, speaker=None, decoder=None) -> None:
        bus._reset_for_tests()
        self.db = _FakeDb()
        self.turns = _FakeTurns(self.db)
        self.api = _FakeCloudApi()
        self.auth = _FakeAuthService(self.db)
        self.speaker = speaker
        self.decoder = decoder
        for registered in filter(None, [decoder]):
            registered.register()
        for registered in filter(None, [speaker]):
            registered.register()
            registered.turns = self.turns
            bus.contribute(POINT_SPOKEN_REPLY, lambda spoken: spoken.ask())
        self.config = config or _config()
        self.service = WhatsAppService(self.config, self.turns, self.db, self.auth, client=self.api)
        self.controllers: list = []
        self.service.listen(self.controllers)
        TurnInput(self.turns, self.db).register()

    async def arrives(self, payload: dict) -> None:
        for incoming in extract_incoming(payload):
            await answered(self.service.receive(incoming))

    def client(self) -> TestClient:
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        router = APIRouter()
        self.controllers[0].register_routes(router)
        app.include_router(router)
        return TestClient(app)


@pytest.fixture
def env() -> Env:
    return Env()


@pytest.fixture
def voice_env() -> Env:
    return Env(speaker=_FakeSpeaker(), decoder=_FakeDecoder())


def _payload(msg_id="wamid.1", sender=LINKED_NUMBER, text="hola", mtype="text") -> dict:
    message = {"from": sender, "id": msg_id, "timestamp": "1749416383", "type": mtype}
    if mtype == "text":
        message["text"] = {"body": text}
    elif mtype == "audio":
        message["audio"] = {"id": "media-in-1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    return _envelope(message, sender)


def _interactive_payload(msg_id="wamid.1", sender=LINKED_NUMBER, kind="button_reply", reply=None) -> dict:
    reply = reply or {"id": "go", "title": "Go"}
    return _envelope({
        "from": sender, "id": msg_id, "timestamp": "1749416383", "type": "interactive",
        "interactive": {"type": kind, kind: reply},
    }, sender)


def _envelope(message: dict, sender: str) -> dict:
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


def _post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/skills/whatsapp/webhook", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature if signature is not None else _sign(body),
        },
    )
