"""The WhatsApp channel's own contract, independent of the real
ChatService/Db/Cloud API: the webhook plumbing (verification handshake,
HMAC signature, dedup, status updates ignored), the identity gate
(unlinked/unregistered numbers never reach a turn), and the turn
orchestration (session bootstrap, replies gathered from what the turn
persisted, non-chat state notice, Markdown flattening).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import struct
from http import HTTPStatus

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
from whatsapp.audio import WHATSAPP_VOICE_MIME, split_wav, wav_to_ogg_opus
from whatsapp.cloud_api_client import split_text
from whatsapp.whatsapp_service import (
    REPLY_ACCEPT_TERMS_LABEL, REPLY_AUDIO_NOT_UNDERSTOOD, REPLY_BUSY, REPLY_DONE, REPLY_INVALID_ACTION,
    REPLY_NO_CHAT_STATE, REPLY_NOT_LINKED, REPLY_NOT_REGISTERED, REPLY_OPTIONS_PROMPT, REPLY_PAUSED, REPLY_REGISTERED,
    REPLY_TECHNICAL_PROBLEM, REPLY_TERMS_ACCEPTED, REPLY_UNSUPPORTED, REPLY_UNSUPPORTED_AUDIO, IncomingMessage,
    WhatsAppService, to_whatsapp_markdown,
)

pytestmark = pytest.mark.contract

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

    async def upload_media(self, data, mime_type, filename="audio.ogg"):
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

    async def mark_read(self, message_id):
        self.read.append(message_id)

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
        # What get_or_create_current_session returns once accept_legal_terms
        # has been called — the resolved shape a real ChatService would
        # reach once the pending gate no longer applies.
        self.resolved_session_payload: dict = {"id": 7}
        self.turn_error: ServiceError | None = None
        self.action_error: Exception | None = None
        self.opening_message: str | None = None
        self.wrap_up_message: str | None = None
        self.calls: list[tuple] = []
        self.state: dict = {"key": "x", "ui_label": "X", "actions": [], "manual_actions": []}
        self.action_reply_message: str | None = None
        self.reply_audio_text: str | None = None
        self.terms_content: str = "Please accept to continue."
        self.accepted_terms_for: list[str] = []

    def get_or_create_current_session(self, session_id):
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

    async def process_turn(self, session_id, text):
        self.calls.append(("turn", Session().user))
        if self.turn_error is not None:
            raise self.turn_error
        self.db.add(session_id, "user", text)
        self.db.add(session_id, "assistant", f"**Hola** — has dicho: {text}", audio_text=self.reply_audio_text)
        return {"session_id": session_id, "state": self.state}

    async def apply_manual_action(self, action_name, session_id):
        self.calls.append(("action", Session().user, action_name))
        if self.action_error is not None:
            raise self.action_error
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

    async def generate(self, text):
        self.spoken.append(text)
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
    return client, service, chat, db, api, talk, listen


def _post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/whatsapp/webhook", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature if signature is not None else _sign(body)},
    )


# --- webhook plumbing ----------------------------------------------------- #

def test_verification_handshake_echoes_challenge(env):
    client, *_ = env
    r = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "42"})
    assert r.status_code == HTTPStatus.OK and r.text == "42"


def test_verification_rejects_wrong_token(env):
    client, *_ = env
    r = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert r.status_code == HTTPStatus.FORBIDDEN


def test_bad_signature_never_reaches_a_turn(env):
    client, _, chat, _, api = env
    assert _post(client, _payload(), signature="sha256=deadbeef").status_code == HTTPStatus.FORBIDDEN
    assert chat.calls == [] and api.sent == []


def test_status_updates_are_ignored():
    payload = {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    assert WhatsAppService.extract_incoming(payload) == []


def test_redelivery_is_deduplicated(env):
    client, _, chat, _, _ = env
    _post(client, _payload(msg_id="wamid.dup"))
    _post(client, _payload(msg_id="wamid.dup"))
    assert [c for c in chat.calls if c[0] == "turn"] == [("turn", LINKED_EMAIL)]


# --- identity gate -------------------------------------------------------- #

def test_unlinked_number_sending_non_text_gets_canned_reply(env):
    client, _, chat, _, api = env
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


def test_unlinked_number_with_an_unknown_code_gets_the_same_message_as_the_web(env):
    client, _, chat, _, api = env
    _post(client, _payload(sender="34699999999", text="NOTACODE"))
    assert chat.calls == []
    assert api.sent == [("34699999999", "This invite link is invalid.")]


def test_unlinked_number_with_a_valid_code_registers_and_welcomes(env):
    client, service, chat, db, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert db.users["34699999999"]["role"] == "user"
    assert chat.calls == [("session", "34699999999")]
    assert api.sent == [("34699999999", REPLY_REGISTERED)]


def test_unlinked_number_with_the_prefixed_wame_text_still_registers(env):
    client, service, _, db, _ = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    _post(client, _payload(sender="34699999999", text="Invitation code: GOODCODE"))
    assert db.users["34699999999"]["role"] == "user"


def test_registration_delivers_the_projects_opening_message_too(env):
    client, service, chat, _, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.opening_message = "Bienvenida."
    _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert [body for _, body in api.sent] == [REPLY_REGISTERED, "Bienvenida."]


def test_unexpected_error_during_redeem_gets_an_apology_not_silence(env):
    client, service, chat, db, api = env
    service._auth_service.unexpected_error = RuntimeError("boom")
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_TECHNICAL_PROBLEM)]
    assert "34699999999" not in db.users


def test_linked_but_unregistered_account_is_refused(env):
    client, _, chat, db, api = env
    db.users[LINKED_NUMBER]["role"] = None
    _post(client, _payload())
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_NOT_REGISTERED)]


def test_non_text_message_gets_courtesy_reply(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="image"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED)]


# --- turn orchestration --------------------------------------------------- #

def test_turn_runs_as_the_linked_account_and_replies_with_persisted_assistant_messages(env):
    client, _, chat, _, api = env
    assert _post(client, _payload(text="hola")).status_code == HTTPStatus.OK
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert api.read == ["wamid.1"]
    # Markdown flattened to WhatsApp's own subset on the way out.
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


def test_no_ai_initiated_opening_message_ahead_of_the_users_own_turn(env):
    """Unlike the invite welcome (test_registration_delivers_the_projects_
    opening_message_too), a normal WhatsApp turn is the user's own —
    prepare_user_initiated_turn never announces the state on its own."""
    client, _, chat, _, api = env
    chat.opening_message = "Bienvenida."
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["*Hola* — has dicho: hola"]


def test_a_chat_blocked_states_own_wrap_up_message_still_goes_out(env):
    client, _, chat, _, api = env
    chat.wrap_up_message = "Conversación finalizada."
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT)
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["Conversación finalizada.", REPLY_NO_CHAT_STATE]


def test_earlier_history_is_not_resent(env):
    client, _, chat, db, api = env
    db.add(7, "assistant", "mensaje de ayer")
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["*Hola* — has dicho: hola"]


def test_non_chat_state_conflict_becomes_a_notice(env):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT)
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_NO_CHAT_STATE)]


def test_non_chat_state_conflict_still_sends_the_states_own_buttons(env):
    """The notice says "use an action instead" — it had better come with
    actions to use, not leave the user stuck with no buttons at all."""
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT)
    chat.state["manual_actions"] = [_action("go", "Go")]
    _post(client, _payload())
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_NO_CHAT_STATE, [("go", "Go")])]


# --- manual actions as buttons/list ---------------------------------------- #

def test_turn_landing_on_a_state_with_two_manual_actions_sends_buttons(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("go", "Go"), _action("stay", "Stay")]
    chat.state["manual_actions"] = chat.state["actions"]
    _post(client, _payload(text="hola"))
    assert api.sent == []
    assert api.interactive == [
        ("button", LINKED_NUMBER, "*Hola* — has dicho: hola", [("go", "Go"), ("stay", "Stay")])
    ]


def test_manual_actions_not_shown_are_excluded(env):
    client, _, chat, _, api = env
    triggered = _action("auto", "Auto", has_trigger=True)
    manual = _action("go", "Go")
    chat.state["actions"] = [triggered, manual]
    chat.state["manual_actions"] = [manual]
    _post(client, _payload(text="hola"))
    assert api.interactive[0][3] == [("go", "Go")]


def test_five_manual_actions_send_a_list(env):
    client, _, chat, _, api = env
    actions = [_action(f"a{i}", f"Action {i}", ui_description=f"Does {i}") for i in range(5)]
    chat.state["actions"] = actions
    chat.state["manual_actions"] = actions
    _post(client, _payload(text="hola"))
    assert len(api.interactive) == 1
    kind, to, body, button_text, rows = api.interactive[0]
    assert kind == "list" and to == LINKED_NUMBER and button_text == "Options"
    assert len(rows) == 5
    assert rows[0] == ("a0", "Action 0", "Does 0")


def test_button_title_longer_than_20_chars_is_truncated(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("go", "A very very long button label indeed")]
    chat.state["manual_actions"] = chat.state["actions"]
    _post(client, _payload(text="hola"))
    _, _, _, buttons = api.interactive[0]
    assert buttons == [("go", "A very very long bu…")]
    assert len(buttons[0][1]) == 20


def test_state_with_no_manual_actions_sends_plain_text_only(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("auto", "Auto", has_trigger=True)]
    chat.state["manual_actions"] = []
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]
    assert api.interactive == []


def test_extract_incoming_ignores_an_unsupported_interactive_reply():
    payload = _interactive_payload(kind="nfm_reply", reply={"response_json": "{}"})
    [message] = WhatsAppService.extract_incoming(payload)
    assert message.type == "interactive" and message.action_id is None


def test_button_reply_applies_the_action_as_the_linked_account(env):
    client, _, chat, _, api = env
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    chat.action_reply_message = "You picked go."
    _post(client, _interactive_payload(kind="button_reply", reply={"id": "go", "title": "Go"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, "You picked go.", [("stay", "Stay")])]


def test_list_reply_applies_the_action_too(env):
    client, _, chat, _, api = env
    chat.action_reply_message = "You picked the list option."
    _post(client, _interactive_payload(kind="list_reply", reply={"id": "opt2", "title": "Option 2"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "opt2")]
    assert api.sent == [(LINKED_NUMBER, "You picked the list option.")]


def test_action_with_no_produced_message_falls_back_to_the_new_states_ui_label(env):
    client, _, chat, _, api = env
    chat.state["ui_label"] = "State Y"
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, "State Y")]


def test_action_with_no_message_and_no_ui_label_falls_back_to_done(env):
    client, _, chat, _, api = env
    chat.state["ui_label"] = None
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_DONE)]


def test_invalid_action_gets_a_notice_and_the_current_states_buttons(env):
    client, _, chat, _, api = env
    chat.action_error = ValueError("Action 'go' not available in state 'x'")
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_INVALID_ACTION, [("stay", "Stay")])]


def test_action_conflict_gets_only_the_busy_notice(env):
    client, _, chat, _, api = env
    chat.action_error = ServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_BUSY)]
    assert api.interactive == []


def test_paused_project_gate_is_relayed(env):
    client, _, chat, _, api = env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]
    assert [c for c in chat.calls if c[0] == "turn"] == []


# --- legal terms ------------------------------------------------------------ #

def test_pending_terms_send_the_content_with_an_accept_button(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_name": "demo-project"}
    chat.terms_content = "## Terms\n\nBe nice."
    _post(client, _payload(text="hola"))
    assert [c for c in chat.calls if c[0] == "turn"] == []
    assert api.sent == []
    kind, to, body, buttons = api.interactive[0]
    assert kind == "button" and to == LINKED_NUMBER
    assert body == "*Terms*\n\nBe nice."
    assert buttons == [("__whatsapp_accept_terms__", REPLY_ACCEPT_TERMS_LABEL)]


def test_registration_with_pending_terms_sends_terms_instead_of_the_welcome(env):
    client, service, chat, db, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.session_payload = {"legal_terms_pending": True, "project_name": "demo-project"}
    _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert db.users["34699999999"]["role"] == "user"
    assert [body for _, body in api.sent] == [REPLY_REGISTERED]
    kind, to, body, buttons = api.interactive[0]
    assert to == "34699999999" and body == "Please accept to continue."
    assert buttons == [("__whatsapp_accept_terms__", REPLY_ACCEPT_TERMS_LABEL)]


def test_accepting_terms_calls_accept_legal_terms_and_bootstraps(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_name": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    chat.opening_message = "Bienvenida."
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert chat.accepted_terms_for == ["demo-project"]
    assert api.sent == [(LINKED_NUMBER, "Bienvenida.")]


def test_accepting_terms_with_no_new_content_gets_a_plain_confirmation(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_name": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert api.sent == [(LINKED_NUMBER, REPLY_TERMS_ACCEPTED)]


def test_accepting_terms_does_not_resend_a_sessions_prior_history(env):
    client, _, chat, db, api = env
    db.add(7, "assistant", "mensaje de ayer")
    chat.session_payload = {"legal_terms_pending": True, "project_name": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert api.sent == [(LINKED_NUMBER, REPLY_TERMS_ACCEPTED)]


async def test_impersonation_does_not_leak_past_the_turn(env):
    # Direct call (no TestClient thread hop) so this context is the one
    # the turn ran in: conftest's default user must be back afterwards.
    _, service, chat, _, _ = env
    await service.handle(IncomingMessage(id="wamid.9", sender=LINKED_NUMBER, type="text", text="hola"))
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert Session().user == "user"


# --- voice in ------------------------------------------------------------- #

def test_voice_note_is_transcribed_and_processed_as_text(voice_env):
    client, _, chat, db, api, talk, listen = voice_env
    _post(client, _payload(mtype="audio"))
    assert listen.heard == [b"OggS-fake-opus"]
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    # The transcript is what got persisted as the user's own message.
    assert [m["content"] for m in db.messages if m["role"] == "user"] == ["hola por voz"]


def test_voice_note_without_listen_service_gets_notice(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED_AUDIO)]


def test_unintelligible_voice_note_gets_notice(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    listen.transcript = "   "
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_transcription_failure_gets_notice_not_exception(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    listen.fail = True
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_media_download_failure_gets_notice(voice_env):
    client, _, chat, _, api, _, _ = voice_env
    api.media.clear()
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_voice_note_from_unlinked_number_is_not_transcribed(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert listen.heard == [] and chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


# --- voice out ------------------------------------------------------------ #

def test_voice_note_in_gets_voice_note_out(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola, te he oído."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola, te he oído."]
    (ogg, mime), = api.uploaded
    assert mime == WHATSAPP_VOICE_MIME and ogg[:4] == b"OggS"
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]
    # Answer in kind: the voice note replaces the text, it doesn't duplicate it.
    assert api.sent == []


def test_text_in_gets_text_out_even_with_voice_available(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


def test_voice_policy_always_speaks_text_replies_too():
    talk = _FakeTalk()
    client, _, chat, _, api = _build(config=_config(voice_replies="always"), talk=talk)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == ["Hola."] and len(api.audio_sent) == 1 and api.sent == []


def test_voice_policy_never_stays_text():
    talk, listen = _FakeTalk(), _FakeListen()
    client, _, chat, _, api = _build(config=_config(voice_replies="never"), talk=talk, listen=listen)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_reply_without_audio_text_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = None
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_voice_note_without_talk_service_falls_back_to_text():
    client, _, chat, _, api = _build(listen=_FakeListen())
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_encoding_error_of_any_kind_falls_back_to_text(voice_env, monkeypatch):
    """wav_to_ogg_opus goes through PyAV, whose own exception types don't
    derive from ValueError/httpx.HTTPError/ImportError — this used to
    escape _try_voice_note uncaught and leave the user with no reply at
    all instead of the text fallback."""
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."

    def _boom(wav):
        raise RuntimeError("pyav exploded")

    monkeypatch.setattr("whatsapp.audio.wav_to_ogg_opus", _boom)
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == [] and api.uploaded == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_upload_failure_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_silent_talk_service_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    talk.silent = True
    _post(client, _payload(mtype="audio"))
    assert api.uploaded == [] and api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_notices_are_never_spoken(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]


def test_spoken_reply_with_manual_actions_gets_buttons_on_a_follow_up(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go"), _action("stop", "Stop")]}
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["audio", "buttons"]
    kind, to, body, buttons = api.interactive[0]
    assert body == REPLY_OPTIONS_PROMPT and [b[0] for b in buttons] == ["go", "stop"]
    assert api.sent == []


def test_spoken_reply_fallback_keeps_buttons_on_the_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go")]}
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["buttons"]
    assert api.interactive[0][2] == "*Hola* — has dicho: hola por voz"


# --- audio encoding ------------------------------------------------------- #

def test_split_wav_handles_streaming_header_and_complete_file():
    pcm, rate = split_wav(_wav(rate=24000))
    assert rate == 24000 and len(pcm) == 12000 * 2  # 0.5 s of 16-bit mono
    streamed = PcmWavCodec.streaming_header(24000) + pcm
    assert split_wav(streamed) == (pcm, 24000)


def test_wav_to_ogg_opus_produces_mono_48k_opus():
    ogg = wav_to_ogg_opus(_wav(seconds=1.0))
    assert ogg[:4] == b"OggS"
    assert b"OpusHead" in ogg[:200]
    # OpusHead: magic(8) version(1) channels(1) ... — channels byte right after version.
    head = ogg.index(b"OpusHead")
    assert ogg[head + 9] == 1
    assert len(ogg) < len(_wav(seconds=1.0)) // 3


def test_wav_to_ogg_opus_rejects_empty_audio():
    with pytest.raises(ValueError):
        wav_to_ogg_opus(PcmWavCodec.streaming_header(22050))


# --- helpers -------------------------------------------------------------- #

def test_markdown_flattening():
    src = "## Título\n\nHola **fuerte** y __otro__, mira [esto](https://x.y).\n\n* uno\n* dos\n- tres"
    assert to_whatsapp_markdown(src) == "*Título*\n\nHola *fuerte* y *otro*, mira esto (https://x.y).\n\n- uno\n- dos\n- tres"


def test_split_text_respects_limit_and_loses_nothing():
    text = ("palabra " * 1000).strip()
    chunks = split_text(text, 4096)
    assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == text
