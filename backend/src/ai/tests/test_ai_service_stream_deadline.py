"""A provider that goes quiet is given up, and the call is cancelled.

Session 55 in production (2026-09-21) sat for 30 s on a Gemini call that
never sent a byte, then the error was lost and the person waited 13
minutes. The wait is bounded here, in AiService, whatever the provider:

- `first_chunk_seconds` (10 s) — the eight replies of that session took
  1.1–2.9 s *in full*; a provider that has not started after 10 s is not
  going to, and a slow start under load still fits.
- `next_chunk_seconds` (10 s) — reset on every chunk: the length of a
  reply never matters, only silence does.
- `silent_round_seconds` (30 s) — a tool round yields nothing until it
  ends (a function call arrives whole from Gemini), so its silence is the
  generation itself: 4096 max output tokens at ≥150 tokens/s ≈ 27 s.

The stall is an AIServiceProviderUnavailableError, so a cascade moves on
and a job may try again; and the cancelled call produces nothing later
— the provider's own stream is torn down, which is what keeps a late
reply from landing as a second message.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from ai import AiService
from ai.response_schema import StringField
from ai.llm_provider import AIServiceProviderUnavailableError, LLMProvider, Thought, ToolCall, ToolCallsRequested, ToolSpec
from ai.stream_deadline import StreamDeadline
from provider_tools_helpers import FakeToolSet

pytestmark = pytest.mark.contract


class _Provider(LLMProvider):
	def __init__(self, first_delay: float, chunks: list[str], gap: float = 0.0) -> None:
		super().__init__()
		self._first_delay = first_delay
		self._chunks = chunks
		self._gap = gap
		self.torn_down = False
		self.advanced = 0

	async def stream_json(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		try:
			await asyncio.sleep(self._first_delay)
			for chunk in self._chunks:
				yield chunk
				await asyncio.sleep(self._gap)
		finally:
			self.torn_down = True

	def advance(self) -> None:
		self.advanced += 1

	def get_input_tokens(self, prompt: str) -> int:
		return 0


class _SilentToolRoundProvider(_Provider):
	def __init__(self, silence: float) -> None:
		super().__init__(0.0, ['{"text": "done"}'])
		self._silence = silence
		self._round = 0

	async def stream_json(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		self._round += 1
		if self._round == 1:
			await asyncio.sleep(self._silence)
			raise ToolCallsRequested(calls=[ToolCall(id="c1", name="lookup", arguments={})], assistant_content=None)
		yield '{"text": "done"}'


async def _reply(service: AiService, tool_set=None) -> str:
	text = ""
	async for delta in service.generate_stream_with_metadata(
		"sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")}, tool_set=tool_set,
	):
		text += delta
	return text


async def test_a_provider_that_never_starts_is_given_up_at_the_first_chunk_deadline():
	provider = _Provider(first_delay=60.0, chunks=['{"text": "late"}'])
	service = AiService(provider, deadline=StreamDeadline(first_chunk_seconds=0.05, next_chunk_seconds=1.0))

	started = time.monotonic()
	with pytest.raises(AIServiceProviderUnavailableError, match="sent nothing for 0.05s"):
		await _reply(service)

	assert time.monotonic() - started < 1.0
	assert provider.torn_down
	assert provider.advanced == 1


async def test_a_reply_is_never_cut_for_its_length_while_chunks_keep_coming():
	chunks = ['{"text": "', "one ", "two ", "three ", "four ", "five", '"}']
	provider = _Provider(first_delay=0.0, chunks=chunks, gap=0.05)
	service = AiService(provider, deadline=StreamDeadline(first_chunk_seconds=0.1, next_chunk_seconds=0.15))

	assert await _reply(service) == "one two three four five"
	assert provider.advanced == 0


async def test_a_reply_that_goes_quiet_half_way_is_given_up_at_the_next_chunk_deadline():
	provider = _Provider(first_delay=0.0, chunks=['{"text": "Hello', ' there"}'], gap=60.0)
	service = AiService(provider, deadline=StreamDeadline(first_chunk_seconds=0.1, next_chunk_seconds=0.1))

	delivered = ""
	with pytest.raises(AIServiceProviderUnavailableError, match="sent nothing for 0.1s"):
		async for delta in service.generate_stream_with_metadata(
			"sys", [], on_metadata=lambda k, v: None, schema={"text": StringField("t")},
		):
			delivered += delta

	assert delivered == "Hello"
	assert provider.torn_down


async def test_a_tool_round_may_stay_silent_for_the_whole_round_deadline():
	provider = _SilentToolRoundProvider(silence=0.2)
	service = AiService(provider, deadline=StreamDeadline(
		first_chunk_seconds=0.05, next_chunk_seconds=0.05, silent_round_seconds=1.0,
	))
	tool_set = FakeToolSet([ToolSpec(name="lookup", description="", parameters={})], ["row"])

	assert await _reply(service, tool_set) == "done"
	assert tool_set.calls == [("lookup", {})]


async def test_a_tool_round_silent_past_the_round_deadline_is_given_up():
	provider = _SilentToolRoundProvider(silence=60.0)
	service = AiService(provider, deadline=StreamDeadline(
		first_chunk_seconds=0.05, next_chunk_seconds=0.05, silent_round_seconds=0.1,
	))
	tool_set = FakeToolSet([ToolSpec(name="lookup", description="", parameters={})], ["row"])

	with pytest.raises(AIServiceProviderUnavailableError, match="sent nothing for 0.1s"):
		await _reply(service, tool_set)

	assert tool_set.calls == []
	assert provider.advanced == 1


class _ThinkingProvider(_Provider):
	def __init__(self, thoughts_at: list[float], text_at: float) -> None:
		super().__init__(0.0, [])
		self._thoughts_at = thoughts_at
		self._text_at = text_at

	async def stream_json(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		try:
			elapsed = 0.0
			for at in self._thoughts_at:
				await asyncio.sleep(at - elapsed)
				elapsed = at
				yield Thought()
			await asyncio.sleep(self._text_at - elapsed)
			yield '{"text": "pondered"}'
		finally:
			self.torn_down = True


def _thinking_deadline() -> StreamDeadline:
	return StreamDeadline(first_thought_seconds=0.1, first_chunk_seconds=0.5, next_chunk_seconds=1.0)


async def test_a_provider_that_sends_not_even_a_thought_is_given_up_at_the_first_thought_deadline():
	provider = _Provider(first_delay=60.0, chunks=['{"text": "late"}'])
	service = AiService(provider, deadline=_thinking_deadline())

	started = time.monotonic()
	with pytest.raises(AIServiceProviderUnavailableError, match="sent nothing for 0.1s"):
		await _reply(service)

	assert time.monotonic() - started < 0.4
	assert provider.torn_down


async def test_a_provider_that_is_thinking_may_write_its_first_text_up_to_the_first_chunk_deadline():
	provider = _ThinkingProvider(thoughts_at=[0.05, 0.25], text_at=0.4)
	service = AiService(provider, deadline=_thinking_deadline())

	assert await _reply(service) == "pondered"
	assert provider.advanced == 0


async def test_a_provider_thinking_past_the_first_chunk_deadline_is_given_up_there():
	provider = _ThinkingProvider(thoughts_at=[0.05, 0.3, 0.45], text_at=60.0)
	service = AiService(provider, deadline=_thinking_deadline())

	started = time.monotonic()
	with pytest.raises(AIServiceProviderUnavailableError, match="was thinking but wrote nothing for 0.5s"):
		await _reply(service)

	assert time.monotonic() - started < 0.9
	assert provider.torn_down
