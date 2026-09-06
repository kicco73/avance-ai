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
from chat.ws_notifications import WsNotifications
from session import Session

pytestmark = pytest.mark.contract

USERNAME = "user"


class _FakeAuthService:
    """Every fake websocket below carries a cookie this always accepts,
    resolving to USERNAME — notification_loop's own auth check isn't
    what these tests are about."""

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


class _FakeWebSocket:
    """Drives WsAdapter.notification_loop without real network/ASGI
    machinery: receive() replays `frames` then raises WebSocketDisconnect,
    and send_json() records every frame sent."""

    def __init__(self, frames: list | None = None):
        self._frames = list(frames or [])
        self.sent: list[dict] = []
        self.cookies = {SESSION_COOKIE_NAME: "fake-token"}
        self.closed_with: int | None = None

    async def accept(self):
        pass

    async def close(self, code: int = 1000):
        self.closed_with = code

    async def receive(self):
        if not self._frames:
            raise WebSocketDisconnect()
        return self._frames.pop(0)

    async def send_json(self, payload: dict):
        self.sent.append(payload)


class TestPush:
    def test_returns_false_when_no_connection_is_registered(self):
        adapter = WsNotifications(_FakeAuthService())

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False

    def test_sends_the_payload_and_returns_true_when_a_connection_exists(self):
        adapter = WsNotifications(_FakeAuthService())
        websocket = _FakeWebSocket()
        adapter._connections[USERNAME] = websocket

        payload = {"type": "notification", "project_name": "proj", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        result = asyncio.run(adapter.push(USERNAME, payload))

        assert result is True
        assert websocket.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self):
        adapter = WsNotifications(_FakeAuthService())
        other_user_socket = _FakeWebSocket()
        adapter._connections["other-user"] = other_user_socket

        result = asyncio.run(adapter.push(USERNAME, {"type": "notification"}))

        assert result is False
        assert other_user_socket.sent == []


class TestNotificationLoopRegistration:
    def test_registers_session_user_immediately_after_accept(self):
        adapter = WsNotifications(_FakeAuthService())
        websocket = _FakeWebSocket()

        asyncio.run(adapter.notification_loop(websocket))

        assert adapter._connections == {}  # disconnected at the end — see the cleanup test below

    def test_ignores_any_frame_the_client_sends(self):
        adapter = WsNotifications(_FakeAuthService())
        registered_during = {}

        class _ObservingWebSocket(_FakeWebSocket):
            async def receive(self):
                registered_during["connected"] = adapter._connections.get(USERNAME) is self
                return await super().receive()

        websocket = _ObservingWebSocket(frames=[{"unexpected": "frame"}])

        asyncio.run(adapter.notification_loop(websocket))

        assert registered_during["connected"] is True
        assert websocket.sent == []


class TestCleanupOnDisconnect:
    def test_the_registration_is_removed_on_disconnect(self):
        adapter = WsNotifications(_FakeAuthService())
        websocket = _FakeWebSocket()

        asyncio.run(adapter.notification_loop(websocket))

        assert USERNAME not in adapter._connections
        assert asyncio.run(adapter.push(USERNAME, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self):
        adapter = WsNotifications(_FakeAuthService())
        other_socket = _FakeWebSocket()
        adapter._connections["other-user"] = other_socket

        closing_socket = _FakeWebSocket()  # no frames at all — disconnects immediately
        asyncio.run(adapter.notification_loop(closing_socket))

        assert adapter._connections == {"other-user": other_socket}
