"""WsAdapter's own username -> WebSocket registry and push (Prompt 13 —
correction to Prompt 12's own (username, project_name) registry). Keyed
by username alone now: the frontend already keeps at most one websocket
per tab, reused across every project's own chat, so a separate
connection per project was never needed. Registered immediately after
accept() under Session().user, not off a frame's own session_id — a
project that's only ever opened, never written to (e.g. an initial
greeting served over REST), used to stay unregistered and unreachable by
push for its entire lifetime; registering at accept() fixes that.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from chat.ws_adapter import WsAdapter
from session import Session

pytestmark = pytest.mark.contract

USERNAME = "user"


@pytest.fixture(autouse=True)
def _session_user():
    """WsAdapter.chat_loop reads Session().user at accept() time — pins
    the process-wide singleton to USERNAME for every test in this file,
    then restores it, so this file can't leak state into any other
    test's own Session()."""
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
    """Drives WsAdapter.chat_loop without any real network/ASGI
    machinery — accept() is a no-op, receive_json() replays `messages`
    one at a time then raises WebSocketDisconnect (same as a real client
    closing the connection), and send_json() just records every frame
    sent, for assertions."""

    def __init__(self, messages: list[dict]):
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def accept(self):
        pass

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
        adapter = WsAdapter(_FakeChatService(), db)

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False

    def test_sends_the_payload_and_returns_true_when_a_connection_exists(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([])
        adapter._connections[USERNAME] = websocket

        payload = {"type": "notification", "project_name": "proj", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        result = asyncio.run(adapter.push(USERNAME, payload))

        assert result is True
        assert websocket.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        other_user_socket = _FakeWebSocket([])
        adapter._connections["other-user"] = other_user_socket

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False
        assert other_user_socket.sent == []


class TestChatLoopRegistration:
    def test_registers_session_user_immediately_after_accept(self, db):
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj")
        adapter = WsAdapter(_FakeChatService(), db)

        pushed = {}

        class _SelfPushingChatService(_FakeChatService):
            async def process_turn(self, sid, text, on_metadata=None):
                # Registration happens at accept(), before this frame is
                # even processed — so a push already works on the very
                # first frame, unlike the old per-frame registration.
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
        session_id = db.create_chat_session(username=USERNAME, project_name="proj")
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert USERNAME not in adapter._connections
        assert asyncio.run(adapter.push(USERNAME, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        other_socket = _FakeWebSocket([])
        adapter._connections["other-user"] = other_socket

        closing_socket = _FakeWebSocket([])  # no messages at all — disconnects immediately
        asyncio.run(adapter.chat_loop(closing_socket))

        assert adapter._connections == {"other-user": other_socket}
