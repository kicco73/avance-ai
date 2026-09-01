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
    REPLY_NO_CHAT_STATE, REPLY_NOT_LINKED, REPLY_NOT_REGISTERED, REPLY_PAUSED, REPLY_TERMS_PENDING,
    REPLY_UNSUPPORTED, IncomingMessage, WhatsAppService, to_whatsapp_markdown,
)

pytestmark = pytest.mark.contract

APP_SECRET = "app-secret"
LINKED_NUMBER = "34600000001"
LINKED_EMAIL = "alice@example.com"


class _FakeCloudApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.read: list[str] = []

    async def send_text(self, to, body):
        self.sent.append((to, body))

    async def mark_read(self, message_id):
        self.read.append(message_id)

    async def close(self):
        pass


class _FakeDb:
    def __init__(self) -> None:
        self.users = {LINKED_EMAIL: {"id": LINKED_EMAIL, "email": LINKED_EMAIL, "role": "user"}}
        self.messages: list[dict] = []

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def get_messages(self, session_id, last_n=None):
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-last_n:] if last_n else rows

    def add(self, session_id, role, content):
        self.messages.append({"id": len(self.messages) + 1, "session_id": session_id, "role": role, "content": content})


class _FakeChatService:
    """Records who it was called as (Session().user) and lets a test
    script the session bootstrap payload and the turn outcome."""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.session_payload: dict = {"id": 7}
        self.turn_error: ServiceError | None = None
        self.opening_message: str | None = None
        self.calls: list[tuple[str, str]] = []

    def get_or_create_current_session(self, session_id):
        self.calls.append(("session", Session().user))
        return self.session_payload

    async def get_messages(self, session_id):
        if self.opening_message and not self.db.get_messages(session_id):
            self.db.add(session_id, "assistant", self.opening_message)
        return self.db.get_messages(session_id)

    async def process_turn(self, session_id, text):
        self.calls.append(("turn", Session().user))
        if self.turn_error is not None:
            raise self.turn_error
        self.db.add(session_id, "user", text)
        self.db.add(session_id, "assistant", f"**Hola** — has dicho: {text}")
        return {"session_id": session_id}


def _config(**overrides) -> WhatsAppServiceConfig:
    values = dict(
        verify_token="my-verify-token", app_secret=APP_SECRET, access_token="tok", phone_number_id="123",
        graph_version="v23.0", users={LINKED_NUMBER: LINKED_EMAIL}, mark_read=True,
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


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def env():
    db = _FakeDb()
    chat = _FakeChatService(db)
    api = _FakeCloudApi()
    service = WhatsAppService(_config(), chat, db, client=api)
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

def test_unlinked_number_gets_canned_reply_and_no_turn(env):
    client, _, chat, _, api = env
    _post(client, _payload(sender="34699999999"))
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


def test_linked_but_unregistered_account_is_refused(env):
    client, _, chat, db, api = env
    del db.users[LINKED_EMAIL]
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
