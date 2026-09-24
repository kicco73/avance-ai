"""Each AiUsage row carries how long its provider call took, measured on
the loop's own clock (see AiService._tap_usage) so a scripted provider
that answers after N seconds records exactly N.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, cast

import pytest

from ai.ai_service import AiService
from ai.llm_provider import LLMProvider, MetadataCallback
from ai.response_schema import StringField
from db.models import AiUsage
from virtual_clock import VirtualClockLoop

pytestmark = pytest.mark.regression


class _RepliesAfterWithUsage(LLMProvider):
    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._seconds = seconds

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        report = cast(MetadataCallback, on_metadata)
        await asyncio.sleep(self._seconds)
        yield json.dumps({"text": "hi"})
        report("input_tokens", 10)
        report("output_tokens", 5)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def test_the_usage_row_records_the_call_s_duration_in_seconds(db):
    ai_service = AiService(_RepliesAfterWithUsage(3.5), db=db)

    async def scenario(clock):
        stream = ai_service.generate_stream_with_metadata("sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")})
        drained = asyncio.ensure_future(_drain(stream))
        await clock.advance(3.5)
        await drained

    with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
        runner.run(scenario(cast(VirtualClockLoop, runner.get_loop()).clock))

    assert AiUsage.get().duration == 3.5


class _ThinksThenReplies(LLMProvider):
    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        report = cast(MetadataCallback, on_metadata)
        yield json.dumps({"text": "hi"})
        report("thoughts_tokens", 700)
        report("input_tokens", 10)
        report("output_tokens", 5)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def test_the_usage_row_records_the_tokens_the_model_spent_thinking(db):
    ai_service = AiService(_ThinksThenReplies(), db=db)

    asyncio.run(_drain(ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")},
    )))

    row = AiUsage.get()
    assert (row.thoughts_tokens, row.input_tokens, row.output_tokens) == (700, 10, 5)


async def _drain(stream) -> None:
    async for _ in stream:
        pass


class _RaisesAfter(LLMProvider):
    def __init__(self, seconds: float, error: Exception) -> None:
        super().__init__()
        self._seconds = seconds
        self._error = error

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        await asyncio.sleep(self._seconds)
        raise self._error
        yield ""

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def test_a_failed_call_is_recorded_with_its_outcome_zero_tokens_and_the_elapsed_duration(db):
    from ai.llm_provider import AIServiceProviderRateLimitedError

    ai_service = AiService(_RaisesAfter(2.0, AIServiceProviderRateLimitedError("nope")), db=db)

    async def scenario(clock):
        with pytest.raises(AIServiceProviderRateLimitedError):
            stream = ai_service.generate_stream_with_metadata("sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")})
            drained = asyncio.ensure_future(_drain(stream))
            await clock.advance(2.0)
            await drained

    with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
        runner.run(scenario(cast(VirtualClockLoop, runner.get_loop()).clock))

    row = AiUsage.get()
    assert (row.outcome, row.input_tokens, row.output_tokens, row.duration) == ("rate_limited", 0, 0, 2.0)


class _TwoChunksWithGap(LLMProvider):
    """First chunk after `first_chunk_seconds`, the rest — and the usage
    report — after `total_seconds` — so time_to_first_chunk and duration
    can be told apart in one row."""
    def __init__(self, first_chunk_seconds: float, total_seconds: float) -> None:
        super().__init__()
        self._first_chunk_seconds = first_chunk_seconds
        self._total_seconds = total_seconds

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        report = cast(MetadataCallback, on_metadata)
        await asyncio.sleep(self._first_chunk_seconds)
        yield '{"text": "h'
        await asyncio.sleep(self._total_seconds - self._first_chunk_seconds)
        yield 'i"}'
        report("input_tokens", 10)
        report("output_tokens", 5)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def test_time_to_first_chunk_is_measured_separately_from_the_call_s_total_duration(db):
    ai_service = AiService(_TwoChunksWithGap(1.5, 4.0), db=db)

    async def scenario(clock):
        stream = ai_service.generate_stream_with_metadata("sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")})
        drained = asyncio.ensure_future(_drain(stream))
        await clock.advance(4.0)
        await drained

    with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
        runner.run(scenario(cast(VirtualClockLoop, runner.get_loop()).clock))

    row = AiUsage.get()
    assert (row.time_to_first_chunk, row.duration) == (1.5, 4.0)
