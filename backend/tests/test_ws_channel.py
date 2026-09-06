"""The one websocket per user as the chat channel (see chat/ws_notifications.py):
`turn` frames read in arrival order with each user message persisted in
that order before any processing, every outgoing frame carrying its own
turn_id, chunks always ahead of their turn's done, a dropped socket never
stopping a turn, and the push-only registry keyed by username.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from chat.ws_notifications import ALREADY_CONNECTED_CLOSE_CODE, HumanNotConnectedError, WsNotifications
from conftest import chat_socket, chat_turn_frames
from session import Session
from test_ws_turn_event_order import _automaton, chat_service_for  # noqa: F401 — a pytest fixture, used by name

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

    async def close(self, code: int = 1000, reason: str | None = None):
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
        channel._connections[USERNAME] = [connection]

        payload = {"type": "notification", "project_name": "proj", "state": {"key": "x"}, "on-enter": "notify('hi')"}
        assert asyncio.run(channel.push(USERNAME, payload)) is True
        assert connection.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self):
        channel = WsNotifications(_FakeAuthService())
        other = _RecordingConnection()
        channel._connections["other-user"] = [other]

        assert asyncio.run(channel.push(USERNAME, {"type": "notification"})) is False
        assert other.sent == []

    def test_broadcasts_to_every_one_of_the_users_own_connections(self):
        channel = WsNotifications(_FakeAuthService())
        first, second = _RecordingConnection(), _RecordingConnection()
        first.id = "conn-1"
        second.id = "conn-2"
        channel._connections[USERNAME] = [first, second]

        payload = {"type": "notification"}
        assert asyncio.run(channel.push(USERNAME, payload)) is True
        assert first.sent == [payload]
        assert second.sent == [payload]

    def test_exclude_connection_id_skips_only_that_connection(self):
        channel = WsNotifications(_FakeAuthService())
        first, second = _RecordingConnection(), _RecordingConnection()
        first.id = "conn-1"
        second.id = "conn-2"
        channel._connections[USERNAME] = [first, second]

        payload = {"type": "human_prompt"}
        assert asyncio.run(channel.push(USERNAME, payload, exclude_connection_id="conn-1")) is True
        assert first.sent == []
        assert second.sent == [payload]

    def test_exclude_connection_id_returns_false_when_it_was_the_only_connection(self):
        channel = WsNotifications(_FakeAuthService())
        only = _RecordingConnection()
        only.id = "conn-1"
        channel._connections[USERNAME] = [only]

        assert asyncio.run(channel.push(USERNAME, {"type": "human_prompt"}, exclude_connection_id="conn-1")) is False
        assert only.sent == []


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
        channel._connections["other-user"] = [other]

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert channel._connections == {"other-user": [other]}

    def test_an_unauthenticated_socket_is_closed_with_4401_before_accept(self):
        class _Rejecting:
            def verify_token(self, token):
                return None

        websocket = _FakeWebSocket()
        asyncio.run(WsNotifications(_Rejecting()).channel_loop(websocket))

        assert websocket.closed_with == 4401


class _FakeAdminAuthService:
    def verify_token(self, token):
        return AuthenticatedUser(provider_user_id="fake", email=USERNAME, name="Fake Admin", picture_url=None, role="admin")


class TestConnectionCap:
    """Per-role cap (see MAX_CONNECTIONS_PER_USER/MAX_CONNECTIONS_PER_ADMIN):
    a connection past it is refused outright with ALREADY_CONNECTED_CLOSE_CODE,
    never silently swapped for an older one — the two-tabs-same-account setup
    HumanTalker testing relies on (see talker.human_talker) is exactly what
    the admin's higher cap makes room for."""

    def test_a_second_connection_for_a_plain_user_is_rejected(self):
        channel = WsNotifications(_FakeAuthService())
        channel._connections[USERNAME] = [_RecordingConnection()]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert websocket.closed_with == ALREADY_CONNECTED_CLOSE_CODE

    def test_an_admin_may_open_a_second_connection(self):
        channel = WsNotifications(_FakeAdminAuthService())
        channel._connections[USERNAME] = [_RecordingConnection()]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert websocket.closed_with is None

    def test_a_third_connection_for_an_admin_is_rejected(self):
        channel = WsNotifications(_FakeAdminAuthService())
        channel._connections[USERNAME] = [_RecordingConnection(), _RecordingConnection()]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert websocket.closed_with == ALREADY_CONNECTED_CLOSE_CODE


class TestHumanPrompt:
    """send_human_prompt/await_human_reply (see chat.ws_human_relay.
    WsHumanRelay): the HumanTalker manual-testing seam."""

    def test_raises_when_the_user_has_no_open_connection(self):
        channel = WsNotifications(_FakeAuthService())

        with pytest.raises(HumanNotConnectedError):
            asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi"))

    def test_a_human_reply_resolves_await_human_reply_with_its_text(self):
        """The operator's own frame carries session_id, never a prompt_id
        it may never have seen — see _current_prompt_for_session."""
        channel = WsNotifications(_FakeAuthService())
        connection = _RecordingConnection()
        connection.id = "conn-1"
        channel._connections[USERNAME] = [connection]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(connection, json.dumps({"type": "human_reply", "session_id": 1, "text": "say hi"}))
            return await channel.await_human_reply(prompt_id)

        assert asyncio.run(scenario()) == "say hi"

    def test_a_human_typing_frame_resolves_wait_for_typing(self):
        channel = WsNotifications(_FakeAuthService())
        connection = _RecordingConnection()
        connection.id = "conn-1"
        channel._connections[USERNAME] = [connection]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(connection, json.dumps({"type": "human_typing", "session_id": 1}))
            await asyncio.wait_for(channel.wait_for_typing(prompt_id), timeout=1.0)

        asyncio.run(scenario())

    def test_await_human_reply_clears_the_sessions_current_prompt(self):
        channel = WsNotifications(_FakeAuthService())
        connection = _RecordingConnection()
        connection.id = "conn-1"
        channel._connections[USERNAME] = [connection]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi")
            channel._handle_frame(connection, json.dumps({"type": "human_reply", "session_id": 1, "text": "ok"}))
            await channel.await_human_reply(prompt_id)
            # A second, unrelated reply for the same session must not
            # resolve a prompt that's already done.
            channel._handle_frame(connection, json.dumps({"type": "human_reply", "session_id": 1, "text": "late"}))
            return channel._current_prompt_for_session.get(1)

        assert asyncio.run(scenario()) is None

    def test_exclude_connection_id_keeps_the_sending_tab_from_seeing_its_own_prompt(self):
        channel = WsNotifications(_FakeAuthService())
        sender, other = _RecordingConnection(), _RecordingConnection()
        sender.id, other.id = "conn-sender", "conn-other"
        channel._connections[USERNAME] = [sender, other]

        asyncio.run(channel.send_human_prompt(
            USERNAME, session_id=1, prompt_text="hi", exclude_connection_id="conn-sender",
        ))

        assert sender.sent == []
        assert len(other.sent) == 1
        assert other.sent[0]["type"] == "human_prompt"

    def test_raises_when_excluding_the_only_connection(self):
        channel = WsNotifications(_FakeAuthService())
        only = _RecordingConnection()
        only.id = "conn-1"
        channel._connections[USERNAME] = [only]

        with pytest.raises(HumanNotConnectedError):
            asyncio.run(channel.send_human_prompt(
                USERNAME, session_id=1, prompt_text="hi", exclude_connection_id="conn-1",
            ))


class _GatedProvider:
    """A real provider's own interface, with the first round held until
    released — so a second `turn` frame is read (and its user message
    persisted) while the first turn is still generating."""

    def __init__(self) -> None:
        self.first_round_started = asyncio.Event()
        self.release = asyncio.Event()
        self.rounds = 0

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.rounds += 1
        if self.rounds == 1:
            self.first_round_started.set()
            await self.release.wait()
        yield '{"text": "answer %d"}' % self.rounds

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


class _ScriptedWebSocket(_FakeWebSocket):
    """Replays `frames`, then holds the connection open until either
    `disconnect_now` is set or `stop_after_finished` turns have finished —
    so a test can watch what the server does while turns are still in
    flight, instead of the socket vanishing the moment the script ends."""

    def __init__(self, frames: list[str], stop_after_finished: int | None = None) -> None:
        super().__init__(frames)
        self._stop_after_finished = stop_after_finished
        self.disconnect_now = asyncio.Event()

    async def receive_text(self):
        if self._frames:
            await asyncio.sleep(0)
            return self._frames.pop(0)
        await self.disconnect_now.wait()
        raise WebSocketDisconnect()

    async def send_json(self, payload: dict):
        self.sent.append(payload)
        finished = len([f for f in self.sent if f.get("type") in ("done", "error")])
        if self._stop_after_finished is not None and finished >= self._stop_after_finished:
            self.disconnect_now.set()


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.005)


def _frames_of(frames: list[dict], turn_id: str) -> list[dict]:
    return [frame for frame in frames if frame.get("turn_id") == turn_id]


@pytest.mark.regression
async def test_two_turn_frames_in_one_tick_persist_the_user_messages_in_frame_order_even_when_the_first_turn_is_slower(
    chat_service_for,
):
    """The ordering guarantee itself: both user messages are on disk, in
    the order their frames arrived, before the first turn has produced
    any reply at all — so it is the socket's own read order that fixes
    the conversation, never how long a turn happens to take."""
    provider = _GatedProvider()
    chat_service = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    channel = WsNotifications(_FakeAuthService(), chat_service)
    websocket = _ScriptedWebSocket(
        [
            json.dumps({"type": "turn", "turn_id": "first", "session_id": session["id"], "text": "I have a problem"}),
            json.dumps({"type": "turn", "turn_id": "second", "session_id": session["id"], "text": "with flight VY3003"}),
        ],
        stop_after_finished=2,
    )

    loop_task = asyncio.create_task(channel.channel_loop(websocket))
    await _wait_for(lambda: len([m for m in db.get_messages(session["id"]) if m["role"] == "user"]) == 2)

    # Both are persisted while the first turn is still inside the provider:
    # nothing of the first reply exists yet.
    assert provider.first_round_started.is_set()
    assert [m["role"] for m in db.get_messages(session["id"])] == ["user", "user"]
    provider.release.set()
    await asyncio.wait_for(loop_task, 5)

    persisted = db.get_messages(session["id"])
    assert [m["role"] for m in persisted] == ["user", "user", "assistant", "assistant"]
    assert [m["content"] for m in persisted if m["role"] == "user"] == ["I have a problem", "with flight VY3003"]

    # Each frame's own turn reported under its own turn_id: a "typing"
    # frame first (see tracking_processor.py's own process()), chunks,
    # then done.
    for turn_id in ("first", "second"):
        own = _frames_of(websocket.sent, turn_id)
        assert own[-1]["type"] == "done", own
        assert own[0]["type"] == "typing", own
        assert set(f["type"] for f in own[1:-1]) == {"chunk"}


@pytest.mark.regression
async def test_a_socket_dropped_mid_turn_still_completes_and_persists_that_turn(chat_service_for):
    provider = _GatedProvider()
    chat_service = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    channel = WsNotifications(_FakeAuthService(), chat_service)
    websocket = _ScriptedWebSocket(
        [json.dumps({"type": "turn", "turn_id": "dropped", "session_id": session["id"], "text": "hello?"})],
    )

    loop_task = asyncio.create_task(channel.channel_loop(websocket))
    await _wait_for(provider.first_round_started.is_set)
    # The browser goes away mid-generation.
    websocket.disconnect_now.set()
    await asyncio.wait_for(loop_task, 5)
    turns = list(channel._turn_tasks)
    provider.release.set()
    await asyncio.wait_for(asyncio.gather(*turns), 5)

    assert [m["role"] for m in db.get_messages(session["id"])] == ["user", "assistant"]
    # The only frame queued before the browser actually left is the
    # "typing" one process() always sends first (see tracking_processor.
    # py) — nothing from the reply itself made it, since generation was
    # still gated behind provider.release at the moment of disconnect.
    assert [f["type"] for f in websocket.sent] == ["typing"]


@pytest.mark.regression
def test_every_outgoing_frame_of_a_turn_carries_its_turn_id_and_chunks_precede_done(client, hello_project):
    session = client.get("/api/chat/session").json()

    frames = chat_turn_frames(client, session["id"], "hi", turn_id="abc-123")

    assert {f["turn_id"] for f in frames} == {"abc-123"}
    assert frames[-1]["type"] == "done"
    # "typing" always precedes generation (see tracking_processor.py's
    # own process()), then every chunk, then done.
    assert frames[0]["type"] == "typing"
    chunks = frames[1:-1]
    assert chunks and set(f["type"] for f in chunks) == {"chunk"}
    assert frames[-1]["reply"][0]["content"] == "".join(f["content"] for f in chunks)


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
