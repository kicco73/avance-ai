from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator, cast

from ai.llm_provider import AIServiceProviderUnavailableError, Thought

FIRST_THOUGHT_SECONDS = 5.0
FIRST_CHUNK_SECONDS = 10.0
NEXT_CHUNK_SECONDS = 10.0
SILENT_ROUND_SECONDS = 30.0


class StreamStalled(AIServiceProviderUnavailableError):
	pass


@dataclass(frozen=True)
class StreamDeadline:
	first_chunk_seconds: float = FIRST_CHUNK_SECONDS
	next_chunk_seconds: float = NEXT_CHUNK_SECONDS
	silent_round_seconds: float = SILENT_ROUND_SECONDS
	first_thought_seconds: float = FIRST_THOUGHT_SECONDS

	def streaming(self, stream: AsyncIterator[str | Thought], label: str) -> AsyncIterator[str | Thought]:
		return self._bound(stream, label, self.first_thought_seconds, self.first_chunk_seconds)

	def tool_round(self, stream: AsyncIterator[str | Thought], label: str) -> AsyncIterator[str | Thought]:
		return self._bound(stream, label, self.silent_round_seconds, self.silent_round_seconds)

	async def _bound(
		self, stream: AsyncIterator[str | Thought], label: str, first_thought_wait: float, first_text_wait: float,
	) -> AsyncIterator[str | Thought]:
		iterator = cast(AsyncGenerator[str | Thought, None], stream.__aiter__())
		loop = asyncio.get_running_loop()
		started = loop.time()
		budget = min(first_thought_wait, first_text_wait)
		deadline = started + budget
		silence = f"sent nothing for {budget:g}s"
		written = False
		try:
			while True:
				try:
					async with asyncio.timeout_at(deadline):
						chunk = await iterator.__anext__()
				except StopAsyncIteration:
					return
				except TimeoutError as exc:
					raise StreamStalled(f"{label} {silence}; the call was cancelled.") from exc
				if isinstance(chunk, Thought):
					if not written:
						deadline = started + first_text_wait
						silence = f"was thinking but wrote nothing for {first_text_wait:g}s"
				else:
					written = True
					deadline = loop.time() + self.next_chunk_seconds
					silence = f"sent nothing for {self.next_chunk_seconds:g}s"
				yield chunk
		finally:
			await iterator.aclose()
