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
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_middleware import AuthMiddleware
from config import WhatsAppServiceConfig
from controllers.whatsapp_controller import WhatsAppController
from service_error import ServiceError
from session import Session
from whatsapp.cloud_api_client import split_text
from whatsapp.whatsapp_service import (
    REPLY_BUSY, REPLY_DONE, REPLY_INVALID_ACTION, REPLY_NO_CHAT_STATE, REPLY_NOT_LINKED, REPLY_NOT_REGISTERED,
    REPLY_PAUSED, REPLY_REGISTERED, REPLY_TERMS_PENDING, REPLY_UNSUPPORTED, IncomingMessage, WhatsAppService,
    to_whatsapp_markdown,
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

    async def send_text(self, to, body):
        self.sent.append((to, body))

    async def send_buttons(self, to, body, buttons):
        self.interactive.append(("button", to, body, buttons))

    async def send_list(self, to, body, button_text, rows):
        self.interactive.append(("list", to, body, button_text, rows))

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

    def add(self, session_id, role, content):
        self.messages.append({"id": len(self.messages) + 1, "session_id": session_id, "role": role, "content": content})


class _FakeAuthService:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.valid_codes: dict[str, str] = {}

    def register_via_whatsapp(self, phone_number, invite_code):
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
        self.turn_error: ServiceError | None = None
        self.action_error: Exception | None = None
        self.opening_message: str | None = None
        self.calls: list[tuple] = []
        self.state: dict = {"key": "x", "ui_label": "X", "actions": [], "manual_actions": []}
        self.action_reply_message: str | None = None

    def get_or_create_current_session(self, session_id):
        self.calls.append(("session", Session().user))
        return self.session_payload

    async def get_messages(self, session_id):
        if self.opening_message and not self.db.get_messages(session_id):
            self.db.add(session_id, "assistant", self.opening_message)
        return self.db.get_messages(session_id)

    def get_state_for_session(self, session_id):
        return self.state

    async def process_turn(self, session_id, text):
        self.calls.append(("turn", Session().user))
        if self.turn_error is not None:
            raise self.turn_error
        self.db.add(session_id, "user", text)
        self.db.add(session_id, "assistant", f"**Hola** — has dicho: {text}")
        return {"session_id": session_id, "state": self.state}

    async def apply_manual_action(self, action_name, session_id):
        self.calls.append(("action", Session().user, action_name))
        if self.action_error is not None:
            raise self.action_error
        if self.action_reply_message:
            self.db.add(session_id, "assistant", self.action_reply_message)
        return {"session_id": session_id, "state": self.state}


def _config(**overrides) -> WhatsAppServiceConfig:
    values = dict(
        verify_token="my-verify-token", app_secret=APP_SECRET, access_token="tok", phone_number_id="123",
        phone_number="15552052260", graph_version="v23.0", mark_read=True,
    )
    values.update(overrides)
    return WhatsAppServiceConfig(**values)


def _payload(msg_id="wamid.1", sender=LINKED_NUMBER, text="hola", mtype="text") -> dict:
    message = {"from": sender, "id": msg_id, "timestamp": "1749416383", "type": mtype}
    if mtype == "text":
        message["text"] = {"body": text}
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


@pytest.fixture
def env():
    db = _FakeDb()
    chat = _FakeChatService(db)
    api = _FakeCloudApi()
    auth = _FakeAuthService(db)
    service = WhatsAppService(_config(), chat, db, auth, client=api)
    app = FastAPI()
    # The real app's login wall sits in front of these routes too — they
    # must be reachable with no cookie at all (role=None).
    app.add_middleware(AuthMiddleware)
    from fastapi import APIRouter
    router = APIRouter()
    WhatsAppController(service).register_routes(router)
    app.include_router(router)
    return TestClient(app), service, chat, db, api


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


def test_registration_delivers_the_projects_opening_message_too(env):
    client, service, chat, _, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.opening_message = "Bienvenida."
    _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert [body for _, body in api.sent] == [REPLY_REGISTERED, "Bienvenida."]


def test_linked_but_unregistered_account_is_refused(env):
    client, _, chat, db, api = env
    db.users[LINKED_NUMBER]["role"] = None
    _post(client, _payload())
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_NOT_REGISTERED)]


def test_non_text_message_gets_courtesy_reply(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="audio"))
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


def test_opening_message_is_sent_before_the_reply(env):
    client, _, chat, _, api = env
    chat.opening_message = "Bienvenida."
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["Bienvenida.", "*Hola* — has dicho: hola"]


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


@pytest.mark.parametrize("payload, reply", [
    ({"paused": True, "paused_reason": "quota"}, REPLY_PAUSED),
    ({"legal_terms_pending": True, "project_name": "p"}, REPLY_TERMS_PENDING),
])
def test_session_bootstrap_gates_are_relayed(env, payload, reply):
    client, _, chat, _, api = env
    chat.session_payload = payload
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, reply)]
    assert [c for c in chat.calls if c[0] == "turn"] == []


async def test_impersonation_does_not_leak_past_the_turn(env):
    # Direct call (no TestClient thread hop) so this context is the one
    # the turn ran in: conftest's default user must be back afterwards.
    _, service, chat, _, _ = env
    await service.handle(IncomingMessage(id="wamid.9", sender=LINKED_NUMBER, type="text", text="hola"))
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert Session().user == "user"


# --- helpers -------------------------------------------------------------- #

def test_markdown_flattening():
    src = "## Título\n\nHola **fuerte** y __otro__, mira [esto](https://x.y).\n\n* uno\n* dos\n- tres"
    assert to_whatsapp_markdown(src) == "*Título*\n\nHola *fuerte* y *otro*, mira esto (https://x.y).\n\n- uno\n- dos\n- tres"


def test_split_text_respects_limit_and_loses_nothing():
    text = ("palabra " * 1000).strip()
    chunks = split_text(text, 4096)
    assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == text
