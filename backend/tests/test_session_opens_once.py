"""A conversation opens once, however many people ask it to.

Two callers asked at the very start: the browser said so, and in the same
breath read the transcript — and reading it opened the conversation too.
Both found nothing said yet and both generated, so a live chat began by
saying the same thing twice. Reading opens nothing now (see
TurnService.read_history), and this is what keeps it opening once.
"""
from __future__ import annotations

import asyncio

import pytest

from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression


class _SlowProvider:
    """Slow enough that a second caller asks while the first is still
    writing — which is the whole of the race, and is what a real model
    does by taking seconds to answer."""

    def __init__(self) -> None:
        self.answers = 0

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.answers += 1
        await asyncio.sleep(0.05)
        yield '{"text": "Welcome."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


async def test_two_callers_opening_at_once_produce_one_message(turn_service_for):
    db = turn_service_for.db
    provider = _SlowProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    opened = await asyncio.gather(
        turn_service.open_if_needed(session["id"]),
        turn_service.open_if_needed(session["id"]),
    )

    said = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert [m["content"] for m in said] == ["Welcome."]
    assert provider.answers == 1
    # One of them ran the turn and reports it; the other has nothing to
    # report, which is how a caller tells "I opened it" from "it was
    # already open".
    assert [result is None for result in opened].count(True) == 1


async def test_reading_the_history_opens_nothing(turn_service_for):
    """The other half, and the reason the race existed at all: being asked
    what has been said used to open the conversation, so a browser that
    said `session.new` and read the history in the same breath opened it
    twice."""
    db = turn_service_for.db
    provider = _SlowProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    assert turn_service.read_history(session["id"]) == []
    assert provider.answers == 0
    assert db.get_messages(session["id"]) == []
