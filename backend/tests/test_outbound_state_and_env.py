"""What a turn says about where it moved and what it wrote. Both leave
from here and nowhere else, so a listener has one vocabulary to learn
(see docs/BUS.md).
"""
from __future__ import annotations

import pytest

from conftest import RecordedMessages
from system.bus import ENV_CHANGED, STATE_CHANGED, STATE_SIGNALS, Message
from turn.outbound import Outbound

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


def _started() -> Message:
    return Message(
        type="input.text", body={"text": "hi"}, username=USERNAME, project_id=PROJECT_ID,
        session_id=7, channel="webchat", origin_id="connection-1",
    )


async def _published(result: dict) -> list[Message]:
    recorded = RecordedMessages(STATE_CHANGED, STATE_SIGNALS, ENV_CHANGED)
    outbound = Outbound(_started())
    outbound.ran(result)
    await outbound.flush()
    return recorded.for_user(USERNAME)


def _result(**overrides) -> dict:
    return {"reply": [], "buttons": [], **overrides}


async def test_a_move_says_where_it_came_from():
    (moved,) = await _published(_result(
        state_changed=True, state={"key": "b"}, from_state="a", new_state="b", triggered_action="go",
    ))

    assert moved.type == STATE_CHANGED
    assert moved.body == {
        "state": {"key": "b"}, "from_state": "a", "new_state": "b", "triggered_action": "go",
    }


async def test_a_self_loop_is_told_apart_by_from_state_alone():
    (moved,) = await _published(_result(
        state_changed=True, state={"key": "a"}, from_state="a", new_state="a", triggered_action="notice",
    ))

    assert moved.body["from_state"] == moved.body["new_state"] == "a"


async def test_every_written_key_is_its_own_message_with_the_turns_envelope():
    published = await _published(_result(env_changed={"counter": 4, "flag": True}))

    assert [message.type for message in published] == [ENV_CHANGED, ENV_CHANGED]
    assert {(m.body["key"], m.body["value"]) for m in published} == {("counter", 4), ("flag", True)}
    for message in published:
        assert (message.project_id, message.session_id) == (PROJECT_ID, 7)
        assert (message.channel, message.origin_id) == ("webchat", "connection-1")


async def test_a_turn_that_wrote_nothing_says_nothing():
    assert await _published(_result(env_changed={})) == []
    assert await _published(_result()) == []


async def test_the_signal_values_a_turn_ran_on_travel_with_the_turns_envelope():
    (evaluated,) = await _published(_result(signals={"mood": 0.5, "focus": 1.0}))

    assert evaluated.type == STATE_SIGNALS
    assert evaluated.body == {"values": {"mood": 0.5, "focus": 1.0}}
    assert (evaluated.project_id, evaluated.session_id) == (PROJECT_ID, 7)


async def test_a_turn_that_measured_nothing_says_nothing_about_signals():
    assert await _published(_result(signals={})) == []
    assert await _published(_result(signals=None)) == []
