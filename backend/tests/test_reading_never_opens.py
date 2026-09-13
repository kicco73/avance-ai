"""Being asked what has been said says nothing.

Reading the transcript used to open the conversation as a side effect, so
a browser that said `session.new` and read the history in the same breath
opened it twice. Opening is asked for now, by whoever has read the
transcript and found nothing in it (see webchat/conversation_opener.py),
and reading is only reading.
"""
from __future__ import annotations

import asyncio

import pytest

from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression


class _CountingProvider:
    def __init__(self) -> None:
        self.answers = 0

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.answers += 1
        await asyncio.sleep(0)
        yield '{"text": "Welcome."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


async def test_reading_the_history_opens_nothing(turn_service_for):
    db = turn_service_for.db
    provider = _CountingProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    assert turn_service.read_history(session["id"]) == []
    assert provider.answers == 0
    assert db.get_messages(session["id"]) == []


async def test_opening_it_is_asked_for_and_speaks(turn_service_for):
    """The other side of the same fact: asked to open, it opens — it does
    not ask itself whether it should."""
    db = turn_service_for.db
    provider = _CountingProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    opened = await turn_service.open_conversation(session["id"])

    assert [m["content"] for m in opened["reply"]] == ["Welcome."]
    assert [m["content"] for m in db.get_messages(session["id"])] == ["Welcome."]
