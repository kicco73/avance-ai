"""WsAdapter's username -> WebSocket registry and push. Keyed by
username alone: the frontend keeps at most one websocket per tab, reused
across every project's chat. Registered immediately after accept() under
Session().user, not off a frame's own session_id.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from chat.ws_adapter import WsAdapter
from session import Session

pytestmark = pytest.mark.contract

USERNAME = "user"


class _FakeAuthService:
    """Every fake websocket below carries a cookie this always accepts,
    resolving to USERNAME — chat_loop's own auth check isn't what these
    tests are about."""

    def verify_token(self, token):
        return AuthenticatedUser(provider_user_id="fake", email=USERNAME, name="Fake User", picture_url=None)


@pytest.fixture(autouse=True)
def _session_user():
    """Pins the process-wide Session().user singleton to USERNAME for
    every test in this file, then restores it."""
    session = Session()
    previous = session.user
    session.user = USERNAME
    yield
    session.user = previous


class _FakeChatService:
    """Only what WsAdapter.chat_loop actually calls — process_turn, with
    the minimal turn-response shape (see tracking_processor.py's own
    _build_turn_response) chat_loop spreads onto its own "done" frame."""

    async def process_turn(self, session_id, text, on_metadata=None):
        return {"session_id": session_id, "state": {"key": "a"}, "reply": []}


class _FakeWebSocket:
    """Drives WsAdapter.chat_loop without real network/ASGI machinery:
    receive_json() replays `messages` then raises WebSocketDisconnect,
    and send_json() records every frame sent."""

    def __init__(self, messages: list[dict]):
        self._messages = list(messages)
        self.sent: list[dict] = []
        self.cookies = {SESSION_COOKIE_NAME: "fake-token"}
        self.closed_with: int | None = None

    async def accept(self):
        pass

    async def close(self, code: int = 1000):
        self.closed_with = code

    async def receive_json(self):
        if not self._messages:
            raise WebSocketDisconnect()
        return self._messages.pop(0)

    async def send_json(self, payload: dict):
        self.sent.append(payload)


def _publish_project(db, project_name: str) -> None:
    db.ensure_project(project_name)
    db.publish_project(project_name)


class TestPush:
    def test_returns_false_when_no_connection_is_registered(self, db):
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False

    def test_sends_the_payload_and_returns_true_when_a_connection_exists(self, db):
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())
        websocket = _FakeWebSocket([])
        adapter._connections[USERNAME] = websocket

        payload = {"type": "notification", "project_name": "proj", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        result = asyncio.run(adapter.push(USERNAME, payload))

        assert result is True
        assert websocket.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self, db):
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())
        other_user_socket = _FakeWebSocket([])
        adapter._connections["other-user"] = other_user_socket

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False
        assert other_user_socket.sent == []


class TestChatLoopRegistration:
    def test_registers_session_user_immediately_after_accept(self, db):
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj", revision=db.get_project_published_revision("proj"))
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())

        pushed = {}

        class _SelfPushingChatService(_FakeChatService):
            async def process_turn(self, sid, text, on_metadata=None):
                # Registration happens at accept(), before this frame is
                # even processed — so a push already works on the first frame.
                pushed["result"] = await adapter.push(USERNAME, {"type": "notification", "probe": True})
                return await super().process_turn(sid, text, on_metadata)

        adapter._chat_service = _SelfPushingChatService()
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert pushed["result"] is True
        assert adapter._connections == {}  # disconnected at the end — see the cleanup test below


class TestCleanupOnDisconnect:
    def test_the_registration_is_removed_on_disconnect(self, db):
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj", revision=db.get_project_published_revision("proj"))
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert USERNAME not in adapter._connections
        assert asyncio.run(adapter.push(USERNAME, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self, db):
        adapter = WsAdapter(_FakeChatService(), db, _FakeAuthService())
        other_socket = _FakeWebSocket([])
        adapter._connections["other-user"] = other_socket

        closing_socket = _FakeWebSocket([])  # no messages at all — disconnects immediately
        asyncio.run(adapter.chat_loop(closing_socket))

        assert adapter._connections == {"other-user": other_socket}
