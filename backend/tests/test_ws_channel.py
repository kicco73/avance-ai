"""The one websocket per user as the chat channel (see chat/ws_notifications.py):
`turn` frames read in arrival order with each user message persisted in
that order before any processing, every outgoing frame carrying its own
turn_id, chunks always ahead of their turn's done, a dropped socket never
stopping a turn, and the push-only registry keyed by username.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from chat.ws_notifications import WsNotifications
from conftest import chat_socket, chat_turn_frames
from session import Session

pytestmark = pytest.mark.contract

USERNAME = "user"


class _FakeAuthService:
    def verify_token(self, token):
        return AuthenticatedUser(provider_user_id="fake", email=USERNAME, name="Fake User", picture_url=None, role="user")


@pytest.fixture(autouse=True)
def _session_user():
    session = Session()
    previous = session.user
    session.user = USERNAME
    yield
    session.user = previous


class _FakeWebSocket:
    """Drives WsNotifications.channel_loop without real network/ASGI
    machinery: receive_text() replays `frames` then raises
    WebSocketDisconnect, and send_json() records every frame sent."""

    def __init__(self, frames: list[str] | None = None):
        self._frames = list(frames or [])
        self.sent: list[dict] = []
        self.cookies = {SESSION_COOKIE_NAME: "fake-token"}
        self.closed_with: int | None = None

    async def accept(self):
        pass

    async def close(self, code: int = 1000):
        self.closed_with = code

    async def receive_text(self):
        if not self._frames:
            raise WebSocketDisconnect()
        await asyncio.sleep(0)
        return self._frames.pop(0)

    async def send_json(self, payload: dict):
        self.sent.append(payload)


class _RecordingConnection:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: dict):
        self.sent.append(payload)


class TestPush:
    def test_returns_false_when_no_connection_is_registered(self):
        channel = WsNotifications(_FakeAuthService())

        assert asyncio.run(channel.push(USERNAME, {"type": "notification"})) is False

    def test_sends_the_payload_and_returns_true_when_a_connection_exists(self):
        channel = WsNotifications(_FakeAuthService())
        connection = _RecordingConnection()
        channel._connections[USERNAME] = connection

        payload = {"type": "notification", "project_name": "proj", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        assert asyncio.run(channel.push(USERNAME, payload)) is True
        assert connection.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self):
        channel = WsNotifications(_FakeAuthService())
        other = _RecordingConnection()
        channel._connections["other-user"] = other

        assert asyncio.run(channel.push(USERNAME, {"type": "notification"})) is False
        assert other.sent == []


class TestChannelLoop:
    def test_a_ping_is_answered_with_a_pong_and_an_unknown_frame_is_ignored(self):
        channel = WsNotifications(_FakeAuthService())
        websocket = _FakeWebSocket(frames=['{"type": "ping"}', 'not json', '{"type": "whatever"}'])

        asyncio.run(channel.channel_loop(websocket))

        assert websocket.sent == [{"type": "pong"}]

    def test_a_pushed_frame_reaches_the_socket_while_connected(self):
        channel = WsNotifications(_FakeAuthService())
        pushed = {}

        class _ObservingWebSocket(_FakeWebSocket):
            async def receive_text(self):
                if "done" not in pushed:
                    pushed["done"] = await channel.push(USERNAME, {"type": "notification", "project_name": "p"})
                    await asyncio.sleep(0)
                return await super().receive_text()

        websocket = _ObservingWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert pushed["done"] is True
        assert websocket.sent == [{"type": "notification", "project_name": "p"}]

    def test_the_registration_is_removed_on_disconnect(self):
        channel = WsNotifications(_FakeAuthService())

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert USERNAME not in channel._connections
        assert asyncio.run(channel.push(USERNAME, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self):
        channel = WsNotifications(_FakeAuthService())
        other = _RecordingConnection()
        channel._connections["other-user"] = other

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert channel._connections == {"other-user": other}

    def test_an_unauthenticated_socket_is_closed_with_4401_before_accept(self):
        class _Rejecting:
            def verify_token(self, token):
                return None

        websocket = _FakeWebSocket()
        asyncio.run(WsNotifications(_Rejecting()).channel_loop(websocket))

        assert websocket.closed_with == 4401


class _SlowFirstTurnAiService:
    """The first turn waits until released; every later one answers at
    once — so the second user message is read while the first turn is
    still generating."""

    def __init__(self) -> None:
        self.first_turn_started = asyncio.Event()
        self.release_first_turn = asyncio.Event()
        self.turns = 0

    def get_models_info(self):
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index):
        pass

    def get_total_tokens(self):
        return 0

    def get_max_output_tokens(self):
        return 4096

    def get_input_tokens(self, prompt):
        return 0

    def is_provider_with_schema(self):
        return True

    def supports_metadata(self):
        return False

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.turns += 1
        if self.turns == 1:
            self.first_turn_started.set()
            await self.release_first_turn.wait()
        yield f"reply {self.turns}"


def _frames_of(frames: list[dict], turn_id: str) -> list[dict]:
    return [frame for frame in frames if frame.get("turn_id") == turn_id]


@pytest.mark.regression
def test_two_turn_frames_in_one_tick_persist_the_user_messages_in_frame_order_even_if_the_first_turn_is_slower(client, hello_project, app_db):
    session = client.get("/api/chat/session").json()
    slow = _SlowFirstTurnAiService()
    chat_service = client.app.state.chat_service
    chat_service._ai_service = slow
    chat_service._tracking_service  # the same TrackingService the app fixture wired; ai_service is passed per turn

    with chat_socket(client) as ws:
        ws.send_json({"type": "turn", "turn_id": "first", "session_id": session["id"], "text": "I have a problem"})
        ws.send_json({"type": "turn", "turn_id": "second", "session_id": session["id"], "text": "with flight VY3003"})
        frames = []
        while len([f for f in frames if f["type"] in ("done", "error")]) < 2:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "chunk" and frame["turn_id"] == "first" and not slow.release_first_turn.is_set():
                pass
            if not slow.release_first_turn.is_set() and slow.first_turn_started.is_set():
                slow.release_first_turn.set()

    user_texts = [m["content"] for m in app_db.get_messages(session["id"]) if m["role"] == "user"]
    assert user_texts == ["I have a problem", "with flight VY3003"]
    for turn_id in ("first", "second"):
        own = _frames_of(frames, turn_id)
        assert own[-1]["type"] == "done", own
        assert all(f["type"] == "chunk" for f in own[:-1])
    first_done = next(i for i, f in enumerate(frames) if f["type"] == "done" and f["turn_id"] == "first")
    assert all(f["turn_id"] == "first" for f in frames[:first_done] if f["type"] == "chunk")


@pytest.mark.regression
def test_every_outgoing_frame_of_a_turn_carries_its_turn_id_and_chunks_precede_done(client, hello_project):
    session = client.get("/api/chat/session").json()

    frames = chat_turn_frames(client, session["id"], "hi", turn_id="abc-123")

    assert {f["turn_id"] for f in frames} == {"abc-123"}
    assert frames[-1]["type"] == "done"
    assert [f["type"] for f in frames[:-1]] and set(f["type"] for f in frames[:-1]) == {"chunk"}
    assert frames[-1]["reply"][0]["content"] == "".join(f["content"] for f in frames[:-1])


@pytest.mark.regression
def test_a_socket_closed_mid_turn_lets_the_turn_complete_and_persist(client, hello_project, app_db):
    session = client.get("/api/chat/session").json()
    slow = _SlowFirstTurnAiService()
    chat_service = client.app.state.chat_service
    chat_service._ai_service = slow

    with chat_socket(client) as ws:
        ws.send_json({"type": "turn", "turn_id": "dropped", "session_id": session["id"], "text": "hello?"})
        # Leave before the reply exists: the connection closes here.

    async def _release_and_wait():
        await asyncio.wait_for(slow.first_turn_started.wait(), 5)
        slow.release_first_turn.set()

    slow.release_first_turn.set()
    deadline = 50
    while deadline and not any(m["role"] == "assistant" for m in app_db.get_messages(session["id"])):
        import time
        time.sleep(0.1)
        deadline -= 1

    roles = [m["role"] for m in app_db.get_messages(session["id"])]
    assert roles == ["user", "assistant"]


@pytest.mark.contract
def test_a_turn_on_someone_elses_session_is_answered_with_an_error_frame(client, hello_project, app_db):
    session = client.get("/api/chat/session").json()

    with chat_socket(client, username="intruder") as ws:
        ws.send_json({"type": "turn", "turn_id": "x", "session_id": session["id"], "text": "hi"})
        frame = ws.receive_json()

    assert frame["type"] == "error"
    assert frame["turn_id"] == "x"
    assert frame["code"] == "session_not_found"
    assert [m for m in app_db.get_messages(session["id"]) if m["role"] == "user"] == []


@pytest.mark.contract
def test_a_turn_on_a_closed_session_is_answered_with_session_closed(client, hello_project):
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/close")

    final = chat_turn_frames(client, session["id"], "hi")[-1]

    assert final["type"] == "error"
    assert final["code"] == "session_closed"
