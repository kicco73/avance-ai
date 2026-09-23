from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import LLMProvider
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression


class _CountingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.answers = 0

    async def stream_json(
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
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    assert turn_service.read_history(session["id"]) == []
    assert provider.answers == 0
    assert db.get_messages(session["id"]) == []


async def test_opening_it_is_asked_for_and_speaks(turn_service_for):
    db = turn_service_for.db
    provider = _CountingProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    opened = await turn_service.open_conversation(session["id"])

    assert [m["content"] for m in opened["reply"]] == ["Welcome."]
    assert [m["content"] for m in db.get_messages(session["id"])] == ["Welcome."]
