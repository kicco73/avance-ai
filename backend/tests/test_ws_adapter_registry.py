"""WsAdapter's own (username, project_name) -> WebSocket registry and
push (Prompt 12) — replaces the old single global `_active_socket`
("single-user prototype: at most one connection matters"), which gave
WakeupService's own cross-project wake-up no channel to reach a session
connected to a project other than whichever one the client happened to
have open. The registry itself is populated fresh off every frame's own
session_id (db.get_chat_session) — resolved this way rather than once at
accept() because project_name isn't knowable that early at all.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from chat.ws_adapter import WsAdapter

pytestmark = pytest.mark.contract

USERNAME = "user"


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

        result = asyncio.run(adapter.push(USERNAME, "proj", {"type": "done"}))

        assert result is False

    def test_sends_the_payload_and_returns_true_when_a_connection_exists(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([])
        adapter._connections[(USERNAME, "proj")] = websocket

        payload = {"type": "done", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        result = asyncio.run(adapter.push(USERNAME, "proj", payload))

        assert result is True
        assert websocket.sent == [payload]

    def test_never_reaches_a_different_projects_own_connection(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        other_project_socket = _FakeWebSocket([])
        adapter._connections[(USERNAME, "other-project")] = other_project_socket

        result = asyncio.run(adapter.push(USERNAME, "proj", {"type": "done"}))

        assert result is False
        assert other_project_socket.sent == []


class TestChatLoopRegistration:
    def test_registers_username_project_name_off_the_first_frames_session_id(self, db):
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj")
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert adapter._connections == {}  # disconnected at the end — see the cleanup test below

    def test_is_reachable_via_push_while_still_connected(self, db):
        """The registration itself has to be observed *during* chat_loop,
        not after it returns (disconnect always clears it, see
        TestCleanupOnDisconnect below) — a second message on the same
        connection is what lets this assert push works mid-loop, right
        after the first message registered it."""
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj")
        adapter = WsAdapter(_FakeChatService(), db)

        pushed = {}

        class _SelfPushingChatService(_FakeChatService):
            async def process_turn(self, sid, text, on_metadata=None):
                pushed["result"] = await adapter.push(USERNAME, "proj", {"type": "done", "probe": True})
                return await super().process_turn(sid, text, on_metadata)

        adapter._chat_service = _SelfPushingChatService()
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert pushed["result"] is True

    def test_an_unresolvable_session_id_never_registers_anything(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([{"message": "hi", "session_id": 999999}])

        asyncio.run(adapter.chat_loop(websocket))

        assert asyncio.run(adapter.push(USERNAME, "proj", {})) is False


class TestCleanupOnDisconnect:
    def test_every_entry_pointing_to_the_closed_socket_is_removed(self, db):
        _publish_project(db, "proj")
        session_id = db.create_chat_session(username=USERNAME, project_name="proj")
        adapter = WsAdapter(_FakeChatService(), db)
        websocket = _FakeWebSocket([{"message": "hi", "session_id": session_id}])

        asyncio.run(adapter.chat_loop(websocket))

        assert (USERNAME, "proj") not in adapter._connections
        assert asyncio.run(adapter.push(USERNAME, "proj", {})) is False

    def test_a_different_connections_own_registration_is_left_alone(self, db):
        adapter = WsAdapter(_FakeChatService(), db)
        other_socket = _FakeWebSocket([])
        adapter._connections[(USERNAME, "other-project")] = other_socket

        closing_socket = _FakeWebSocket([])  # no messages at all — disconnects immediately
        asyncio.run(adapter.chat_loop(closing_socket))

        assert adapter._connections == {(USERNAME, "other-project"): other_socket}
