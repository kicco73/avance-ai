"""A live conversation belongs to one channel at a time, and this is the
half of that the chat window owns: telling the browser that the
conversation it is showing is being had somewhere else.

`current` on a session payload says only that the session is the one its
type's active slot holds — never that *you* may write to it (see
TurnService._session_payload). Writability is that AND the session having
been opened on your own channel, and this package is the one object in
the process that knows which channel that is. So it narrows `current` on
the way out, and everything the browser already does for an inactive
session — the disabled composer, the notice, the hidden actions — follows
with no new concept on the wire.

It does not take the conversation back. `session.enter` goes out on every
reload and every reconnection of the socket, and a takeover there would
steal the conversation from the phone each time a forgotten tab woke up.
Taking it back is something the person does (`session.create`).
"""
from __future__ import annotations

import pytest

from system import bus
from system.bus import SESSION_ENDED, SESSION_INFO, STATE_SIGNALS, Message
from webchat.webchat_service import WebchatService

pytestmark = pytest.mark.contract

CONNECTION = "connection-1"
SESSION = 7


class _FakeConnections:

    def __init__(self) -> None:
        self.to_connection: list[dict] = []
        self.to_watchers: list[dict] = []
        self.watched: list[int] = []
        self.unwatched: list[int] = []

    def has_connection(self, connection_id: str) -> bool:
        return connection_id == CONNECTION

    def send_to_connection(self, connection_id: str, payload: dict) -> bool:
        self.to_connection.append(payload)
        return True

    def send_to_watchers(self, session_id: int, payload: dict) -> int:
        self.to_watchers.append(payload)
        return 1

    def watch_session(self, connection_id: str, session_id: int) -> None:
        self.watched.append(session_id)

    def unwatch_session(self, session_id: int) -> None:
        self.unwatched.append(session_id)


def _listening() -> _FakeConnections:
    """The chat window, registered on the Bus the way its skill registers
    it: what reaches the browser is what a listener took off the Bus, not
    what a test handed the service by hand."""
    connections = _FakeConnections()
    WebchatService(None, connections, None).register()
    return connections


def _informing(channel: str | None, current: bool = True) -> Message:
    return Message(
        type=SESSION_INFO, username="user", session_id=SESSION, origin_id=CONNECTION,
        body={"state": {}, "services": {}, "current": current, "channel": channel},
    )


async def test_a_conversation_open_on_another_channel_reaches_the_browser_as_not_writable():
    connections = _listening()

    await bus.publish(_informing("whatsapp"))

    (frame,) = connections.to_connection
    assert frame["current"] is False
    assert frame["channel"] == "whatsapp"


async def test_its_own_conversation_is_left_alone():
    connections = _listening()

    await bus.publish(_informing("webchat"))

    assert connections.to_connection[0]["current"] is True


async def test_a_conversation_that_belongs_to_no_channel_is_left_alone():
    """test, preview and imported sessions are nobody's channel (see
    SessionTypeStrategy.caller_channel) — narrowing them would take the
    editor's own Run chat down with WhatsApp."""
    connections = _listening()

    await bus.publish(_informing(None))

    assert connections.to_connection[0]["current"] is True


async def test_a_session_already_superseded_stays_superseded():
    connections = _listening()

    await bus.publish(_informing("webchat", current=False))

    assert connections.to_connection[0]["current"] is False


async def test_a_closed_conversation_is_announced_to_its_watchers_and_then_stops_being_watched():
    """The announcement has no connection to answer to, so it goes to
    whoever is showing that conversation — and after it there is nothing
    left to show them: a socket that kept watching a closed session went
    on being told about it for as long as it stayed open."""
    connections = _listening()

    await bus.publish(Message(
        type=SESSION_ENDED, username="user", session_id=SESSION, body={"reason": "channel-switch"},
    ))

    assert [frame["reason"] for frame in connections.to_watchers] == ["channel-switch"]
    assert connections.unwatched == [SESSION]


async def test_the_signal_values_of_a_turn_reach_the_connection_that_asked_for_it():
    """What the Inspect panel's own bars read. The frame is a turn's, so
    it goes back to whoever asked — the Run tab's embedded chat, not
    every connection showing the conversation."""
    connections = _listening()

    await bus.publish(Message(
        type=STATE_SIGNALS, username="user", session_id=SESSION, origin_id=CONNECTION,
        body={"values": {"mood": 0.5}},
    ))

    assert connections.to_connection == [
        {"type": STATE_SIGNALS, "session_id": SESSION, "values": {"mood": 0.5}},
    ]
