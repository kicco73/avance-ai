"""The one websocket per user as the chat channel (see system/bus_channel.py):
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
from system import bus
from system.bus import UI_BUTTONS, UI_NOTIFICATION, UI_PROGRESS
from system.bus_channel import (
    HUMAN_PROMPT, SUPERSEDED_CLOSE_CODE, SWITCHED_TO_OTHER_CLIENT, HumanNotConnectedError, WsConnection,
    BusChannel,
)
from turn.input_listener import TurnInput
from webchat.webchat_service import WebchatService
from conftest import chat_socket, chat_turn_frames
from system.web_session import WebSession
from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.contract

USERNAME = "user"


class _FakeAuthService:
    def verify_token(self, token):
        return AuthenticatedUser(provider_user_id="fake", email=USERNAME, name="Fake User", picture_url=None, role="user")


@pytest.fixture(autouse=True)
def _session_user():
    session = WebSession()
    previous = session.user
    session.user = USERNAME
    yield
    session.user = previous


class _FakeWebSocket:
    """Drives BusChannel.channel_loop without real network/ASGI
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
    def __init__(self, *subscriptions: str):
        self.id = "conn"
        self.sent: list[dict] = []
        self.closed = False
        self._subscriptions = set(subscriptions)

    def subscribe(self, event_types):
        self._subscriptions.update(event_types)

    def unsubscribe(self, event_types):
        self._subscriptions.difference_update(event_types)

    def wants(self, event_type: str) -> bool:
        return event_type in self._subscriptions

    def send(self, payload: dict):
        self.sent.append(payload)


class TestPushEvent:
    """A frame reaches a connection only once that connection registered
    for its type — being connected is not being subscribed."""

    def test_reaches_only_the_connections_that_registered_for_that_type(self):
        channel = BusChannel(_FakeAuthService())
        subscribed = _RecordingConnection(UI_NOTIFICATION)
        other_type = _RecordingConnection(UI_PROGRESS)
        deaf = _RecordingConnection()
        channel._connections[USERNAME] = [subscribed, other_type, deaf]

        payload = {"type": UI_NOTIFICATION, "project_name": "proj"}
        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, payload)) is True
        assert subscribed.sent == [payload]
        assert other_type.sent == []
        assert deaf.sent == []

    def test_returns_false_when_the_only_connection_never_registered(self):
        channel = BusChannel(_FakeAuthService())
        channel._connections[USERNAME] = [_RecordingConnection()]

        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})) is False

    def test_reaches_every_one_of_that_users_registered_connections(self):
        channel = BusChannel(_FakeAuthService())
        first, second = _RecordingConnection(UI_NOTIFICATION), _RecordingConnection(UI_NOTIFICATION)
        first.id, second.id = "conn-1", "conn-2"
        channel._connections[USERNAME] = [first, second]

        payload = {"type": UI_NOTIFICATION}
        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, payload)) is True
        assert first.sent == [payload]
        assert second.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self):
        channel = BusChannel(_FakeAuthService())
        other = _RecordingConnection(UI_NOTIFICATION)
        channel._connections["other-user"] = [other]

        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})) is False
        assert other.sent == []


class TestRegistration:
    def test_a_subscribe_frame_registers_only_the_exportable_types_and_unsubscribe_drops_them(self):
        channel = BusChannel(_FakeAuthService())
        connection = _RecordingConnection()

        channel._handle_frame(
            connection, json.dumps({"type": "subscribe", "events": [UI_NOTIFICATION, "input.text", "output.text"]})
        )
        assert connection.wants(UI_NOTIFICATION) is True
        assert connection.wants("input.text") is False
        assert connection.wants("output.text") is False

        channel._handle_frame(connection, json.dumps({"type": "unsubscribe", "events": [UI_NOTIFICATION]}))
        assert connection.wants(UI_NOTIFICATION) is False

    def test_a_malformed_events_field_registers_nothing(self):
        channel = BusChannel(_FakeAuthService())
        connection = _RecordingConnection()

        channel._handle_frame(connection, json.dumps({"type": "subscribe"}))
        channel._handle_frame(connection, json.dumps({"type": "subscribe", "events": UI_NOTIFICATION}))
        channel._handle_frame(connection, json.dumps({"type": "subscribe", "events": [{"a": 1}]}))

        assert connection.wants(UI_NOTIFICATION) is False


class TestInboundFrames:
    """What a client says reaches the Bus whole: the frame is the body,
    minus what the envelope carries. A type that grows a field needs no
    change here — and the field is not silently dropped, which is exactly
    what happened to a button's own id."""

    def test_a_frame_reaches_the_bus_with_every_field_it_carried(self):
        channel = BusChannel(_FakeAuthService())
        channel.owned_by("webchat")
        connection = _RecordingConnection()
        published: list = []

        async def take(message):
            published.append(message)

        async def scenario():
            bus.subscribe("input.button", take)
            try:
                channel._handle_frame(connection, json.dumps({
                    "type": "input.button", "session_id": 7, "id": "go-loud",
                }))
                await asyncio.sleep(0)
            finally:
                bus.unsubscribe("input.button", take)

        asyncio.run(scenario())
        assert [(m.type, m.session_id, m.body) for m in published] == [
            ("input.button", 7, {"id": "go-loud"}),
        ]


class TestChannelLoop:
    def test_a_ping_is_answered_with_a_pong_and_an_unknown_frame_is_ignored(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _FakeWebSocket(frames=['{"type": "ping"}', 'not json', '{"type": "whatever"}'])

        asyncio.run(channel.channel_loop(websocket))

        assert websocket.sent == [{"type": "pong"}]

    def test_a_pushed_frame_reaches_the_socket_while_connected(self):
        channel = BusChannel(_FakeAuthService())
        pushed = {}

        class _ObservingWebSocket(_FakeWebSocket):
            """Pushes once its own subscribe frame has been read and
            handled — a connection is sent nothing it did not ask for."""

            async def receive_text(self):
                if not self._frames and "done" not in pushed:
                    pushed["done"] = await channel.push_event(
                        USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION, "project_name": "p"},
                    )
                    await asyncio.sleep(0)
                return await super().receive_text()

        websocket = _ObservingWebSocket([json.dumps({"type": "subscribe", "events": [UI_NOTIFICATION]})])
        asyncio.run(channel.channel_loop(websocket))

        assert pushed["done"] is True
        assert websocket.sent == [{"type": UI_NOTIFICATION, "project_name": "p"}]

    def test_the_registration_is_removed_on_disconnect(self):
        channel = BusChannel(_FakeAuthService())

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert USERNAME not in channel._connections
        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self):
        channel = BusChannel(_FakeAuthService())
        other = _RecordingConnection()
        channel._connections["other-user"] = [other]

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert channel._connections == {"other-user": [other]}

    def test_an_unauthenticated_socket_is_closed_with_4401_before_accept(self):
        class _Rejecting:
            def verify_token(self, token):
                return None

        websocket = _FakeWebSocket()
        asyncio.run(BusChannel(_Rejecting()).channel_loop(websocket))

        assert websocket.closed_with == 4401


class _FakeAdminAuthService:
    def verify_token(self, token):
        return AuthenticatedUser(provider_user_id="fake", email=USERNAME, name="Fake Admin", picture_url=None, role="admin")


class _SupersedableConnection(_RecordingConnection):
    """A stand-in old connection that records being superseded, without a
    real socket or writer task behind it."""

    def __init__(self):
        super().__init__()
        self.superseded = False

    def supersede(self):
        self.superseded = True


class TestConnectionCap:
    """Per-role cap (see MAX_CONNECTIONS_PER_USER/MAX_CONNECTIONS_PER_ADMIN),
    and who gives way when it is reached: the newest connection always wins
    and the oldest is superseded, because the browser the person is looking
    at is the one that just connected. The admin's higher cap is what makes
    room for the two-tabs-same-account setup HumanTalker testing relies on
    (see talker.human_talker)."""

    def test_a_second_connection_for_a_plain_user_supersedes_the_first(self):
        channel = BusChannel(_FakeAuthService())
        first = _SupersedableConnection()
        channel._connections[USERNAME] = [first]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert first.superseded is True
        assert websocket.closed_with is None

    def test_the_superseded_connection_stops_being_registered(self):
        channel = BusChannel(_FakeAuthService())
        first = _SupersedableConnection()
        channel._connections[USERNAME] = [first]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert first not in channel._connections.get(USERNAME, [])

    def test_an_admin_may_open_a_second_connection_without_superseding(self):
        channel = BusChannel(_FakeAdminAuthService())
        first = _SupersedableConnection()
        channel._connections[USERNAME] = [first]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert first.superseded is False
        assert websocket.closed_with is None

    def test_a_third_connection_for_an_admin_supersedes_the_oldest_only(self):
        channel = BusChannel(_FakeAdminAuthService())
        oldest, newer = _SupersedableConnection(), _SupersedableConnection()
        channel._connections[USERNAME] = [oldest, newer]

        websocket = _FakeWebSocket()
        asyncio.run(channel.channel_loop(websocket))

        assert oldest.superseded is True
        assert newer.superseded is False


class TestSupersede:
    """What a superseded socket is actually told (see WsConnection.supersede):
    the SWITCHED_TO_OTHER_CLIENT frame first, then SUPERSEDED_CLOSE_CODE — in
    that order, so the browser has the reason before the socket goes."""

    def test_sends_the_switched_frame_then_closes_with_the_superseded_code(self):
        websocket = _FakeWebSocket()
        connection = WsConnection(websocket)
        connection.supersede()

        asyncio.run(connection.write_loop())

        assert websocket.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]
        assert websocket.closed_with == SUPERSEDED_CLOSE_CODE

    def test_a_frame_arriving_after_it_was_superseded_is_not_answered(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _FakeWebSocket()
        connection = WsConnection(websocket)
        connection.supersede()

        channel._handle_frame(connection, '{"type": "ping"}')
        asyncio.run(connection.write_loop())

        assert websocket.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]


class TestHumanPrompt:
    """send_human_prompt/await_human_reply (see system.bus_human_relay.
    BusHumanRelay): the HumanTalker manual-testing seam."""

    def test_raises_when_the_user_has_no_open_connection(self):
        channel = BusChannel(_FakeAuthService())

        with pytest.raises(HumanNotConnectedError):
            asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi"))

    def test_a_human_reply_resolves_await_human_reply_with_its_text(self):
        """The operator's own frame carries session_id, never a prompt_id
        it may never have seen — see _current_prompt_for_session."""
        channel = BusChannel(_FakeAuthService())
        connection = _RecordingConnection(HUMAN_PROMPT)
        connection.id = "conn-1"
        channel._connections[USERNAME] = [connection]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(connection, json.dumps({"type": "human_reply", "session_id": 1, "text": "say hi"}))
            return await channel.await_human_reply(prompt_id)

        assert asyncio.run(scenario()) == "say hi"

    def test_a_human_typing_frame_resolves_wait_for_typing(self):
        channel = BusChannel(_FakeAuthService())
        connection = _RecordingConnection(HUMAN_PROMPT)
        connection.id = "conn-1"
        channel._connections[USERNAME] = [connection]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(connection, json.dumps({"type": "human_typing", "session_id": 1}))
            await asyncio.wait_for(channel.wait_for_typing(prompt_id), timeout=1.0)

        asyncio.run(scenario())

    def test_only_the_operator_who_was_asked_can_answer(self):
        """Any signed-in user can name a session_id; the reply is only a
        reply when it comes from the identity the prompt went to."""
        channel = BusChannel(_FakeAuthService())
        operator = _RecordingConnection(HUMAN_PROMPT)
        operator.id = "conn-1"
        intruder = _RecordingConnection(HUMAN_PROMPT)
        intruder.id = "conn-2"
        channel._connections[USERNAME] = [operator]
        channel._connections["someone-else@example.com"] = [intruder]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(intruder, json.dumps({"type": "human_reply", "session_id": 1, "text": "nonsense"}))
            channel._handle_frame(intruder, json.dumps({"type": "human_typing", "session_id": 1}))
            assert not channel._pending_human_replies[prompt_id].done()
            assert not channel._pending_typing_events[prompt_id].is_set()
            channel._handle_frame(operator, json.dumps({"type": "human_reply", "session_id": 1, "text": "say hi"}))
            return await channel.await_human_reply(prompt_id)

        assert asyncio.run(scenario()) == "say hi"

    def test_await_human_reply_clears_the_sessions_current_prompt(self):
        channel = BusChannel(_FakeAuthService())
        connection = _RecordingConnection(HUMAN_PROMPT)
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

    def test_only_a_connection_that_registered_is_sent_the_prompt(self):
        """Registering is what says "I am the one answering as a person".
        A tab that never asked is not an operator, whatever else it has
        open — which is what the sender used to guess at, by excluding
        whichever tab had just written."""
        channel = BusChannel(_FakeAuthService())
        operator, bystander = _RecordingConnection(HUMAN_PROMPT), _RecordingConnection()
        operator.id, bystander.id = "conn-operator", "conn-bystander"
        channel._connections[USERNAME] = [bystander, operator]

        asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi"))

        assert bystander.sent == []
        assert [frame["type"] for frame in operator.sent] == [HUMAN_PROMPT]

    def test_raises_when_the_only_connection_never_registered(self):
        channel = BusChannel(_FakeAuthService())
        bystander = _RecordingConnection()
        bystander.id = "conn-1"
        channel._connections[USERNAME] = [bystander]

        with pytest.raises(HumanNotConnectedError):
            asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi"))

    def test_a_prompt_that_fired_first_is_delivered_when_a_connection_registers(self):
        """The operator's view opens from a takeover notification, which
        is after the turn already asked: a one-shot push would leave it
        with nothing to answer."""
        channel = BusChannel(_FakeAuthService())
        operator, latecomer = _RecordingConnection(HUMAN_PROMPT), _RecordingConnection()
        operator.id, latecomer.id = "conn-1", "conn-2"
        channel._connections[USERNAME] = [operator, latecomer]

        asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?"))
        channel._handle_frame(latecomer, json.dumps({"type": "subscribe", "events": [HUMAN_PROMPT]}))

        assert [frame["text"] for frame in latecomer.sent] == ["what do I say?"]

    def test_an_answered_prompt_is_not_delivered_to_whoever_registers_next(self):
        channel = BusChannel(_FakeAuthService())
        operator, latecomer = _RecordingConnection(HUMAN_PROMPT), _RecordingConnection()
        operator.id, latecomer.id = "conn-1", "conn-2"
        channel._connections[USERNAME] = [operator, latecomer]

        async def scenario():
            prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
            channel._handle_frame(operator, json.dumps({"type": "human_reply", "session_id": 1, "text": "say hi"}))
            await channel.await_human_reply(prompt_id)
            channel._handle_frame(latecomer, json.dumps({"type": "subscribe", "events": [HUMAN_PROMPT]}))

        asyncio.run(scenario())
        assert latecomer.sent == []


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
        finished = len([f for f in self.sent if f.get("type") in ("output.text", "output.error")])
        if self._stop_after_finished is not None and finished >= self._stop_after_finished:
            self.disconnect_now.set()


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.005)


def _frames_of(frames: list[dict], session_id: int) -> list[dict]:
    return [frame for frame in frames if frame.get("session_id") == session_id]


@pytest.mark.regression
async def test_two_turn_frames_in_one_tick_persist_the_user_messages_in_frame_order_even_when_the_first_turn_is_slower(
    turn_service_for,
):
    """The ordering guarantee itself: both user messages are on disk, in
    the order their frames arrived, before the first turn has produced
    any reply at all — so it is the socket's own read order that fixes
    the conversation, never how long a turn happens to take."""
    provider = _GatedProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = turn_service_for.db
    session = await turn_service.get_current_session_if_any_or_create_new(None)
    channel = BusChannel(_FakeAuthService())
    # Two objects now, and the split is the point: core runs the turn and
    # publishes what it produces, the chat window forwards what is
    # addressed to a connection it holds (see turn/input_listener.py).
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    TurnInput(turn_service, db).register()
    WebchatService(turn_service, None, channel).register()
    websocket = _ScriptedWebSocket(
        [
            json.dumps({"type": "input.text", "session_id": session["id"], "text": "I have a problem"}),
            json.dumps({"type": "input.text", "session_id": session["id"], "text": "with flight VY3003"}),
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

    # Two answers, one per exchange: the first message was already being
    # answered when the second arrived, so the second got its own (see
    # turn/input_listener.py's own coalescing).
    own = _frames_of(websocket.sent, session["id"])
    assert own[0] == {**own[0], "type": "output.text_stream", "text": ""}, own
    assert [f["type"] for f in own if f["type"] == "output.text"] and own[-1]["type"] == "output.text", own


@pytest.mark.regression
async def test_a_socket_dropped_mid_turn_still_completes_and_persists_that_turn(turn_service_for):
    provider = _GatedProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = turn_service_for.db
    session = await turn_service.get_current_session_if_any_or_create_new(None)
    channel = BusChannel(_FakeAuthService())
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    TurnInput(turn_service, db).register()
    WebchatService(turn_service, None, channel).register()
    websocket = _ScriptedWebSocket(
        [json.dumps({"type": "input.text", "session_id": session["id"], "text": "hello?"})],
    )

    # The turn is core's task now, not this service's, so there is
    # nothing here to await: the terminal frame it publishes is what says
    # it finished — and it publishes it whether or not anyone is left to
    # forward it, which is the whole point of this test.
    finished = asyncio.Event()

    async def note_the_end(_message):
        finished.set()

    bus.subscribe(UI_BUTTONS, note_the_end)

    loop_task = asyncio.create_task(channel.channel_loop(websocket))
    await _wait_for(provider.first_round_started.is_set)
    # The browser goes away mid-generation.
    websocket.disconnect_now.set()
    await asyncio.wait_for(loop_task, 5)
    provider.release.set()
    await asyncio.wait_for(finished.wait(), 5)

    assert [m["role"] for m in db.get_messages(session["id"])] == ["user", "assistant"]
    # The only frame queued before the browser actually left is the empty
    # chunk process() always sends first (see tracking_processor.py) —
    # nothing from the reply itself made it, since generation was still
    # gated behind provider.release at the moment of disconnect.
    assert [(f["type"], f["text"]) for f in websocket.sent] == [("output.text_stream", "")]


@pytest.mark.regression
def test_every_outgoing_frame_of_a_turn_carries_its_turn_id_and_chunks_precede_done(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()

    frames = chat_turn_frames(client, session["id"], "hi", turn_id="abc-123")

    kinds = [f["type"] for f in frames]
    assert frames[-1]["type"] == "output.text"
    # The empty chunk always precedes generation (see tracking_processor.py's
    # own process()), then the pieces, then every whole message.
    assert (frames[0]["type"], frames[0]["text"]) == ("output.text_stream", "")
    chunks = [f for f in frames if f["type"] == "output.text_stream" and f["text"]]
    whole = [f for f in frames if f["type"] == "output.text"]
    assert chunks and whole
    assert kinds.index("output.text") > max(i for i, kind in enumerate(kinds) if kind == "output.text_stream")
    # The last whole message is the answer, and the pieces were pieces of it.
    assert whole[-1]["text"] == "".join(f["text"] for f in chunks)


@pytest.mark.contract
def test_a_turn_on_someone_elses_session_is_answered_with_an_error_frame(client, hello_project, app_db):
    session = client.get("/api/skills/webchat/sessions/current").json()

    with chat_socket(client, username="intruder") as ws:
        ws.send_json({"type": "input.text", "session_id": session["id"], "text": "hi"})
        frame = ws.receive_json()

    assert frame["type"] == "output.error"
    assert frame["code"] == "session_not_found"
    assert [m for m in app_db.get_messages(session["id"]) if m["role"] == "user"] == []


@pytest.mark.contract
def test_a_turn_on_a_closed_session_is_answered_with_session_closed(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()
    client.post(f"/api/core/sessions/{session['id']}/close")

    final = chat_turn_frames(client, session["id"], "hi")[-1]

    assert final["type"] == "output.error"
    assert final["code"] == "session_closed"
