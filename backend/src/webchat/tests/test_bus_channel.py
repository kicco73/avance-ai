"""The one websocket per user as the chat channel (see system/bus_channel.py):
`turn` frames read in arrival order with each user message persisted in
that order before any processing, every outgoing frame carrying its own
turn_id, chunks always ahead of their turn's done, a dropped socket never
stopping a turn, and the push-only registry keyed by username.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import WebSocketDisconnect

from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from system import bus
from system.bus import INPUT_TEXT, STATE_BUTTONS, UI_NOTIFICATION, UI_PROGRESS, Message
from system.bus_channel import (
    HUMAN_PROMPT, SUPERSEDED_CLOSE_CODE, SWITCHED_TO_OTHER_CLIENT, HumanNotConnectedError, WsConnection,
    BusChannel,
)
from turn.input_listener import TurnInput
from webchat.webchat_service import WebchatService
from conftest import chat_socket, chat_turn_frames, enter_chat, session_of
from webchat.tests.webchat_helpers import end_chat
from system.web_session import WebSession
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.contract

USERNAME = "user"
OTHER_USERNAME = "other-user"


class _FakeAuthService:
    """Resolves whatever identity the cookie names, the way the real one
    does — so two sockets of one channel can be two different people."""

    def __init__(self, role: str = "user") -> None:
        self._role = role

    def verify_token(self, token):
        return AuthenticatedUser(
            provider_user_id=token, email=token, name="Fake User", picture_url=None, role=self._role,
        )


class _FakeAdminAuthService(_FakeAuthService):
    def __init__(self) -> None:
        super().__init__(role="admin")


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
    WebSocketDisconnect, and send_json() records every frame sent. The
    session cookie names the identity, as a browser's does."""

    def __init__(self, frames: list[str] | None = None, username: str = USERNAME):
        self._frames = list(frames or [])
        self.sent: list[dict] = []
        self.cookies = {SESSION_COOKIE_NAME: username}
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


class _OpenWebSocket(_FakeWebSocket):
    """A socket a test holds open and feeds one frame at a time, so it can
    watch what the server does between two frames of the same connection.
    It opens the way a browser does, by registering for `events`."""

    def __init__(self, *events: str, username: str = USERNAME) -> None:
        super().__init__(username=username)
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.idle = asyncio.Event()
        if events:
            self.say({"type": "subscribe", "events": list(events)})

    def say(self, frame: dict) -> None:
        self.idle.clear()
        self._inbox.put_nowait(json.dumps(frame))

    def hang_up(self) -> None:
        self._inbox.put_nowait(None)

    async def settle(self) -> None:
        """Back when everything this socket said has been handled."""
        await asyncio.wait_for(self.idle.wait(), 5)

    async def receive_text(self):
        if self._inbox.empty():
            self.idle.set()
        raw = await self._inbox.get()
        if raw is None:
            raise WebSocketDisconnect()
        return raw


@asynccontextmanager
async def _connected(channel: BusChannel, *websockets: _OpenWebSocket):
    """Every socket driven through channel_loop — the only way in there
    is — and held open for the body: a registration exists only while
    its own loop is running, and the sockets register in the order they
    are named here."""
    loops = [asyncio.create_task(channel.channel_loop(websocket)) for websocket in websockets]
    for websocket in websockets:
        await websocket.settle()
    try:
        yield
    finally:
        for websocket in websockets:
            websocket.hang_up()
        await asyncio.wait_for(asyncio.gather(*loops), 5)


async def _connection_id_of(websocket: _OpenWebSocket) -> str:
    """The id this socket's own frames arrive under, which is what a
    listener answers to (see BusChannel.send_to_connection). A connection
    is never told its id; the Bus message it produced carries it."""
    seen: list[str] = []

    async def take(message):
        seen.append(message.origin_id)

    bus.subscribe(INPUT_TEXT, take)
    try:
        websocket.say({"type": INPUT_TEXT, "text": "who am I"})
        await websocket.settle()
        await _wait_for(lambda: seen)
    finally:
        bus.unsubscribe(INPUT_TEXT, take)
    return seen[0]


class TestPushEvent:
    """A frame reaches a connection only once that connection registered
    for its type — being connected is not being subscribed."""

    def test_reaches_only_the_connections_that_registered_for_that_type(self):
        channel = BusChannel(_FakeAdminAuthService())
        subscribed, other_type = _OpenWebSocket(UI_NOTIFICATION), _OpenWebSocket(UI_PROGRESS)
        payload = {"type": UI_NOTIFICATION, "project_name": "proj"}
        reached = {}

        async def scenario():
            async with _connected(channel, subscribed, other_type):
                reached["pushed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, payload)

        asyncio.run(scenario())

        assert reached["pushed"] is True
        assert subscribed.sent == [payload]
        assert other_type.sent == []

    def test_returns_false_when_the_only_connection_never_registered(self):
        channel = BusChannel(_FakeAuthService())
        deaf = _OpenWebSocket()
        reached = {}

        async def scenario():
            async with _connected(channel, deaf):
                reached["pushed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert reached["pushed"] is False
        assert deaf.sent == []

    def test_reaches_every_one_of_that_users_registered_connections(self):
        channel = BusChannel(_FakeAdminAuthService())
        first, second = _OpenWebSocket(UI_NOTIFICATION), _OpenWebSocket(UI_NOTIFICATION)
        payload = {"type": UI_NOTIFICATION}
        reached = {}

        async def scenario():
            async with _connected(channel, first, second):
                reached["pushed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, payload)

        asyncio.run(scenario())

        assert reached["pushed"] is True
        assert first.sent == [payload]
        assert second.sent == [payload]

    def test_never_reaches_a_different_users_own_connection(self):
        channel = BusChannel(_FakeAuthService())
        other = _OpenWebSocket(UI_NOTIFICATION, username=OTHER_USERNAME)
        reached = {}

        async def scenario():
            async with _connected(channel, other):
                reached["pushed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert reached["pushed"] is False
        assert other.sent == []


class _FailsOnNthSendWebSocket(_OpenWebSocket):
    """Raises instead of sending its `fail_on`-th frame — a transient
    write failure (a serialization error, a momentary network blip) that
    has nothing to do with the socket actually disconnecting."""

    def __init__(self, *events: str, fail_on: int, username: str = USERNAME) -> None:
        super().__init__(*events, username=username)
        self._fail_on = fail_on
        self._attempts = 0

    async def send_json(self, payload: dict) -> None:
        self._attempts += 1
        if self._attempts == self._fail_on:
            raise RuntimeError("simulated transient send failure")
        self.sent.append(payload)


class TestWriterSurvivesASendFailure:
    """One frame failing to serialize/send must not be allowed to take
    every frame after it down with it — the browser is still connected
    (`receive_text` never raised), so the server has no reason yet to
    believe otherwise."""

    def test_a_send_failure_does_not_silently_stop_later_deliveries(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _FailsOnNthSendWebSocket(UI_NOTIFICATION, fail_on=2)
        pushed = {}

        async def scenario():
            async with _connected(channel, websocket):
                pushed["first"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION, "n": 1})
                await asyncio.sleep(0)
                pushed["second"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION, "n": 2})
                await asyncio.sleep(0)
                pushed["third"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION, "n": 3})
                await asyncio.sleep(0)

        asyncio.run(scenario())

        assert pushed == {"first": True, "second": True, "third": True}
        assert websocket.sent == [{"type": UI_NOTIFICATION, "n": 1}, {"type": UI_NOTIFICATION, "n": 3}]


class TestRegistration:
    """What a `subscribe` frame did is read where it shows: a type the
    connection registered for reaches it, one it was refused does not."""

    def test_a_subscribe_frame_registers_only_the_exportable_types_and_unsubscribe_drops_them(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _OpenWebSocket(UI_NOTIFICATION, INPUT_TEXT, "output.text")
        reached = {}

        async def scenario():
            async with _connected(channel, websocket):
                reached["allowed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})
                reached["refused"] = await channel.push_event(USERNAME, "output.text", {"type": "output.text"})
                websocket.say({"type": "unsubscribe", "events": [UI_NOTIFICATION]})
                await websocket.settle()
                reached["dropped"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert reached == {"allowed": True, "refused": False, "dropped": False}

    def test_a_malformed_events_field_registers_nothing(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _OpenWebSocket()
        reached = {}

        async def scenario():
            async with _connected(channel, websocket):
                for frame in (
                    {"type": "subscribe"},
                    {"type": "subscribe", "events": UI_NOTIFICATION},
                    {"type": "subscribe", "events": [{"a": 1}]},
                ):
                    websocket.say(frame)
                    await websocket.settle()
                reached["pushed"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert reached["pushed"] is False


class TestInboundFrames:
    """What a client says reaches the Bus whole: the frame is the body,
    minus what the envelope carries. A type that grows a field needs no
    change here — and the field is not silently dropped, which is exactly
    what happened to a button's own id."""

    def test_a_frame_reaches_the_bus_with_every_field_it_carried(self):
        channel = BusChannel(_FakeAuthService())
        channel.owned_by("webchat")
        websocket = _OpenWebSocket()
        published: list = []

        async def take(message):
            published.append(message)

        async def scenario():
            bus.subscribe("input.button", take)
            try:
                async with _connected(channel, websocket):
                    websocket.say({"type": "input.button", "session_id": 7, "id": "go-loud"})
                    await websocket.settle()
                    await _wait_for(lambda: published)
            finally:
                bus.unsubscribe("input.button", take)

        asyncio.run(scenario())
        assert [(m.type, m.session_id, m.body) for m in published] == [
            ("input.button", 7, {"id": "go-loud"}),
        ]


class TestWebForwarding:
    """The other direction: a WEB_FORWARDED message published anywhere
    arrives on the socket under its own type, its body flat beside it and
    the conversation it is about in the envelope."""

    def test_a_forwarded_message_arrives_as_a_frame_of_its_own_type(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _OpenWebSocket(UI_NOTIFICATION)

        async def scenario():
            async with _connected(channel, websocket):
                await bus.publish(Message(
                    type=UI_NOTIFICATION, body={"project_name": "proj"}, username=USERNAME, session_id=9,
                ))

        asyncio.run(scenario())

        assert websocket.sent == [{"type": UI_NOTIFICATION, "project_name": "proj", "session_id": 9}]

    def test_a_forwarded_message_never_reaches_a_connection_that_did_not_register(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _OpenWebSocket(UI_PROGRESS)

        async def scenario():
            async with _connected(channel, websocket):
                await bus.publish(Message(type=UI_NOTIFICATION, body={}, username=USERNAME))

        asyncio.run(scenario())

        assert websocket.sent == []


class TestChannelLoop:
    def test_a_ping_is_answered_with_a_pong_and_an_unknown_frame_is_ignored(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _FakeWebSocket(frames=['{"type": "ping"}', 'not json', '{"type": "whatever"}'])

        asyncio.run(channel.channel_loop(websocket))

        assert websocket.sent == [{"type": "pong"}]

    def test_a_pushed_frame_reaches_the_socket_while_connected(self):
        channel = BusChannel(_FakeAuthService())
        websocket = _OpenWebSocket(UI_NOTIFICATION)
        reached = {}

        async def scenario():
            async with _connected(channel, websocket):
                reached["pushed"] = await channel.push_event(
                    USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION, "project_name": "p"},
                )

        asyncio.run(scenario())

        assert reached["pushed"] is True
        assert websocket.sent == [{"type": UI_NOTIFICATION, "project_name": "p"}]

    def test_the_registration_is_removed_on_disconnect(self):
        channel = BusChannel(_FakeAuthService())

        asyncio.run(channel.channel_loop(_FakeWebSocket()))

        assert asyncio.run(channel.push_event(USERNAME, UI_NOTIFICATION, {})) is False

    def test_a_different_users_own_registration_is_left_alone(self):
        channel = BusChannel(_FakeAuthService())
        other = _OpenWebSocket(UI_NOTIFICATION, username=OTHER_USERNAME)
        leaving = _OpenWebSocket(UI_NOTIFICATION)
        reached = {}

        async def scenario():
            async with _connected(channel, other):
                async with _connected(channel, leaving):
                    pass
                reached["other"] = await channel.push_event(OTHER_USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})
                reached["gone"] = await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert reached == {"other": True, "gone": False}
        assert other.sent == [{"type": UI_NOTIFICATION}]

    def test_an_unauthenticated_socket_is_closed_with_4401_before_accept(self):
        class _Rejecting:
            def verify_token(self, token):
                return None

        websocket = _FakeWebSocket()
        asyncio.run(BusChannel(_Rejecting()).channel_loop(websocket))

        assert websocket.closed_with == 4401


PRIVILEGED_ROLES = ("customer", "supervisor", "admin")


class TestConnectionCap:
    """Per-role cap (see MAX_CONNECTIONS_PER_USER/MAX_CONNECTIONS_PER_PRIVILEGED_ROLE),
    and who gives way when it is reached: the newest connection always wins
    and the oldest is superseded, because the browser the person is looking
    at is the one that just connected. The higher cap — every role ranked
    "customer" or above, not "user" itself — is what makes room for a
    privileged tab's own preview/test chat (App Store, Manage Projects, the
    project editor's Run tab, each opening a second connection of their
    own) without superseding the tab's primary connection, the same seam
    the two-tabs-same-account setup HumanTalker testing relies on (see
    talker.human_talker)."""

    def test_a_second_connection_for_a_plain_user_supersedes_the_first(self):
        channel = BusChannel(_FakeAuthService())
        first, second = _OpenWebSocket(), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, first):
                async with _connected(channel, second):
                    pass

        asyncio.run(scenario())

        assert first.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]
        assert first.closed_with == SUPERSEDED_CLOSE_CODE
        assert second.sent == [] and second.closed_with is None

    def test_the_superseded_connection_is_sent_nothing_more(self):
        channel = BusChannel(_FakeAuthService())
        first, second = _OpenWebSocket(UI_NOTIFICATION), _OpenWebSocket(UI_NOTIFICATION)

        async def scenario():
            async with _connected(channel, first):
                async with _connected(channel, second):
                    await channel.push_event(USERNAME, UI_NOTIFICATION, {"type": UI_NOTIFICATION})

        asyncio.run(scenario())

        assert first.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]
        assert second.sent == [{"type": UI_NOTIFICATION}]

    @pytest.mark.parametrize("role", PRIVILEGED_ROLES)
    def test_a_privileged_role_may_open_a_second_connection_without_superseding(self, role):
        channel = BusChannel(_FakeAuthService(role=role))
        first, second = _OpenWebSocket(), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, first):
                async with _connected(channel, second):
                    pass

        asyncio.run(scenario())

        assert first.sent == [] and first.closed_with is None
        assert second.sent == [] and second.closed_with is None

    @pytest.mark.parametrize("role", PRIVILEGED_ROLES)
    def test_a_third_connection_for_a_privileged_role_supersedes_the_oldest_only(self, role):
        channel = BusChannel(_FakeAuthService(role=role))
        oldest, newer, newest = _OpenWebSocket(), _OpenWebSocket(), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, oldest, newer):
                async with _connected(channel, newest):
                    pass

        asyncio.run(scenario())

        assert oldest.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]
        assert oldest.closed_with == SUPERSEDED_CLOSE_CODE
        assert newer.sent == [] and newest.sent == []


class TestSupersede:
    """What a superseded socket is actually told (see WsConnection.supersede):
    the SWITCHED_TO_OTHER_CLIENT frame first, then SUPERSEDED_CLOSE_CODE — in
    that order, so the browser has the reason before the socket goes."""

    def test_sends_the_switched_frame_then_closes_with_the_superseded_code(self):
        websocket = _FakeWebSocket()
        connection = WsConnection(websocket, USERNAME)
        connection.supersede()

        asyncio.run(connection.write_loop())

        assert websocket.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]
        assert websocket.closed_with == SUPERSEDED_CLOSE_CODE

    def test_a_superseded_connection_stops_being_addressable_at_all(self):
        """Its id names nothing and it watches nothing, from the moment
        it is superseded rather than from whenever its own loop ends: an
        answer addressed to it falls through to whoever is showing that
        conversation (see webchat_service._deliver) instead of being
        written into a socket that discards it."""
        channel = BusChannel(_FakeAuthService())
        channel.owned_by("webchat")
        first, second = _OpenWebSocket(), _OpenWebSocket()
        observed = {}

        async def scenario():
            async with _connected(channel, first):
                first_id = await _connection_id_of(first)
                channel.watch_session(first_id, 7)
                async with _connected(channel, second):
                    observed["addressable"] = channel.has_connection(first_id)
                    observed["answered"] = channel.send_to_connection(first_id, {"type": "output.text"})
                    observed["watchers"] = channel.send_to_watchers(7, {"type": "session.ended"})

        asyncio.run(scenario())

        assert observed == {"addressable": False, "answered": False, "watchers": 0}
        assert first.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]

    def test_a_frame_arriving_after_it_was_superseded_is_not_answered(self):
        channel = BusChannel(_FakeAuthService())
        first, second = _OpenWebSocket(), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, first):
                async with _connected(channel, second):
                    first.say({"type": "ping"})
                    await first.settle()

        asyncio.run(scenario())

        assert first.sent == [{"type": SWITCHED_TO_OTHER_CLIENT}]


class TestHumanPrompt:
    """send_human_prompt/await_human_reply (see system.bus_human_relay.
    BusHumanRelay): the HumanTalker manual-testing seam."""

    def test_raises_when_the_user_has_no_open_connection(self):
        channel = BusChannel(_FakeAuthService())

        with pytest.raises(HumanNotConnectedError):
            asyncio.run(channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi"))

    def test_a_human_reply_resolves_await_human_reply_with_its_text(self):
        """The operator's own frame carries session_id, never a prompt_id
        it may never have seen — see _prompt_by_session."""
        channel = BusChannel(_FakeAuthService())
        operator = _OpenWebSocket(HUMAN_PROMPT)

        async def scenario():
            async with _connected(channel, operator):
                prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
                operator.say({"type": "human_reply", "session_id": 1, "text": "say hi"})
                await operator.settle()
                return await channel.await_human_reply(prompt_id)

        assert asyncio.run(scenario()) == "say hi"

    def test_a_human_typing_frame_resolves_wait_for_typing(self):
        channel = BusChannel(_FakeAuthService())
        operator = _OpenWebSocket(HUMAN_PROMPT)

        async def scenario():
            async with _connected(channel, operator):
                prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
                operator.say({"type": "human_typing", "session_id": 1})
                await operator.settle()
                await asyncio.wait_for(channel.wait_for_typing(prompt_id), timeout=1.0)

        asyncio.run(scenario())

    def test_only_the_operator_who_was_asked_can_answer(self):
        """Any signed-in user can name a session_id; the reply is only a
        reply when it comes from the identity the prompt went to."""
        channel = BusChannel(_FakeAuthService())
        operator = _OpenWebSocket(HUMAN_PROMPT)
        intruder = _OpenWebSocket(HUMAN_PROMPT, username="someone-else@example.com")

        async def scenario():
            async with _connected(channel, operator, intruder):
                prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
                intruder.say({"type": "human_reply", "session_id": 1, "text": "nonsense"})
                intruder.say({"type": "human_typing", "session_id": 1})
                await intruder.settle()
                operator.say({"type": "human_reply", "session_id": 1, "text": "say hi"})
                await operator.settle()
                return await channel.await_human_reply(prompt_id)

        assert asyncio.run(scenario()) == "say hi"

    def test_a_stale_reply_never_resolves_the_sessions_next_prompt(self):
        """A reply that arrives after the prompt it answered was resolved
        belongs to nothing: the session's next prompt is a new question,
        and answering it with the previous answer is the bug this shape
        prevents."""
        channel = BusChannel(_FakeAuthService())
        operator = _OpenWebSocket(HUMAN_PROMPT)

        async def scenario():
            async with _connected(channel, operator):
                first = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi")
                operator.say({"type": "human_reply", "session_id": 1, "text": "ok"})
                await operator.settle()
                assert await channel.await_human_reply(first) == "ok"

                operator.say({"type": "human_reply", "session_id": 1, "text": "late"})
                await operator.settle()
                second = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="and now?")
                operator.say({"type": "human_reply", "session_id": 1, "text": "second"})
                await operator.settle()
                return await channel.await_human_reply(second)

        assert asyncio.run(scenario()) == "second"

    def test_only_a_connection_that_registered_is_sent_the_prompt(self):
        """Registering is what says "I am the one answering as a person".
        A tab that never asked is not an operator, whatever else it has
        open — which is what the sender used to guess at, by excluding
        whichever tab had just written."""
        channel = BusChannel(_FakeAdminAuthService())
        bystander, operator = _OpenWebSocket(), _OpenWebSocket(HUMAN_PROMPT)

        async def scenario():
            async with _connected(channel, bystander, operator):
                await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi")

        asyncio.run(scenario())

        assert bystander.sent == []
        assert [frame["type"] for frame in operator.sent] == [HUMAN_PROMPT]

    def test_raises_when_the_only_connection_never_registered(self):
        channel = BusChannel(_FakeAuthService())
        bystander = _OpenWebSocket()

        async def scenario():
            async with _connected(channel, bystander):
                with pytest.raises(HumanNotConnectedError):
                    await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="hi")

        asyncio.run(scenario())

    def test_a_prompt_that_fired_first_is_delivered_when_a_connection_registers(self):
        """The operator's view opens from a takeover notification, which
        is after the turn already asked: a one-shot push would leave it
        with nothing to answer."""
        channel = BusChannel(_FakeAdminAuthService())
        operator, latecomer = _OpenWebSocket(HUMAN_PROMPT), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, operator, latecomer):
                await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
                latecomer.say({"type": "subscribe", "events": [HUMAN_PROMPT]})
                await latecomer.settle()

        asyncio.run(scenario())

        assert [frame["text"] for frame in latecomer.sent] == ["what do I say?"]

    def test_an_answered_prompt_is_not_delivered_to_whoever_registers_next(self):
        channel = BusChannel(_FakeAdminAuthService())
        operator, latecomer = _OpenWebSocket(HUMAN_PROMPT), _OpenWebSocket()

        async def scenario():
            async with _connected(channel, operator, latecomer):
                prompt_id = await channel.send_human_prompt(USERNAME, session_id=1, prompt_text="what do I say?")
                operator.say({"type": "human_reply", "session_id": 1, "text": "say hi"})
                await operator.settle()
                await channel.await_human_reply(prompt_id)
                latecomer.say({"type": "subscribe", "events": [HUMAN_PROMPT]})
                await latecomer.settle()

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
    """The ordering guarantee itself: both user messages are in the
    transcript, in the order their frames arrived, before the first turn
    has produced any reply at all — so it is the socket's own read order
    that fixes the conversation, never how long a turn happens to take.
    On disk each lands with the reply that answers it (see
    turn/turn_transaction.py)."""
    provider = _GatedProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    channel = BusChannel(_FakeAuthService())
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    TurnInput(turn_service, db).register()
    WebchatService(turn_service, channel, None).register()
    websocket = _ScriptedWebSocket(
        [
            json.dumps({"type": "input.text", "session_id": session["id"], "text": "I have a problem"}),
            json.dumps({"type": "input.text", "session_id": session["id"], "text": "with flight VY3003"}),
        ],
        stop_after_finished=2,
    )

    loop_task = asyncio.create_task(channel.channel_loop(websocket))
    await _wait_for(lambda: len([m for m in turn_service.read_history(session["id"]) if m["role"] == "user"]) == 2)
    assert provider.first_round_started.is_set()
    assert [m["role"] for m in turn_service.read_history(session["id"])] == ["user", "user"]
    assert db.get_messages(session["id"]) == []
    provider.release.set()
    await asyncio.wait_for(loop_task, 5)

    persisted = db.get_messages(session["id"])
    assert [m["role"] for m in persisted] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in persisted if m["role"] == "user"] == ["I have a problem", "with flight VY3003"]
    own = _frames_of(websocket.sent, session["id"])
    assert own[0] == {**own[0], "type": "output.text_stream", "text": ""}, own
    assert [f["type"] for f in own[-2:]] == ["output.text", "state.buttons"], own


@pytest.mark.regression
async def test_a_socket_dropped_mid_turn_still_completes_and_persists_that_turn(turn_service_for):
    provider = _GatedProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    channel = BusChannel(_FakeAuthService())
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    TurnInput(turn_service, db).register()
    WebchatService(turn_service, channel, None).register()
    websocket = _ScriptedWebSocket(
        [json.dumps({"type": "input.text", "session_id": session["id"], "text": "hello?"})],
    )
    finished = asyncio.Event()

    async def note_the_end(_message):
        finished.set()

    bus.subscribe(STATE_BUTTONS, note_the_end)

    loop_task = asyncio.create_task(channel.channel_loop(websocket))
    await _wait_for(provider.first_round_started.is_set)
    websocket.disconnect_now.set()
    await asyncio.wait_for(loop_task, 5)
    provider.release.set()
    await asyncio.wait_for(finished.wait(), 5)

    assert [m["role"] for m in db.get_messages(session["id"])] == ["user", "assistant"]
    assert [(f["type"], f["text"]) for f in websocket.sent] == [("output.text_stream", "")]


@pytest.mark.regression
def test_every_outgoing_frame_of_a_turn_carries_its_turn_id_and_chunks_precede_done(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    frames = chat_turn_frames(client, session_id, "hi", turn_id="abc-123")

    kinds = [f["type"] for f in frames]
    assert frames[-1]["type"] == "state.buttons"
    assert (frames[0]["type"], frames[0]["text"]) == ("output.text_stream", "")
    chunks = [f for f in frames if f["type"] == "output.text_stream" and f["text"]]
    whole = [f for f in frames if f["type"] == "output.text"]
    assert chunks and whole
    assert kinds.index("output.text") > max(i for i, kind in enumerate(kinds) if kind == "output.text_stream")
    assert whole[-1]["text"] == "".join(f["text"] for f in chunks)


@pytest.mark.contract
def test_a_turn_on_someone_elses_session_is_answered_with_an_error_frame(client, hello_project, app_db):
    session_id = session_of(enter_chat(client, hello_project))

    with chat_socket(client, username="intruder") as ws:
        ws.send_json({"type": "input.text", "session_id": session_id, "text": "hi"})
        frame = ws.receive_json()

    assert frame["type"] == "output.error"
    assert frame["code"] == "session_not_found"
    assert [m for m in app_db.get_messages(session_id) if m["role"] == "user"] == []


@pytest.mark.contract
def test_a_turn_on_a_closed_session_is_answered_with_session_closed(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))
    end_chat(client, session_id)

    final = chat_turn_frames(client, session_id, "hi")[-1]

    assert final["type"] == "output.error"
    assert final["code"] == "session_closed"
