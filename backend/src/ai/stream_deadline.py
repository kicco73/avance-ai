from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator, cast

from ai.llm_provider import AIServiceProviderUnavailableError

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

	def streaming(self, stream: AsyncIterator[str], label: str) -> AsyncIterator[str]:
		return self._bound(stream, label, self.first_chunk_seconds)

	def tool_round(self, stream: AsyncIterator[str], label: str) -> AsyncIterator[str]:
		return self._bound(stream, label, self.silent_round_seconds)

	async def _bound(self, stream: AsyncIterator[str], label: str, first_wait: float) -> AsyncIterator[str]:
		iterator = cast(AsyncGenerator[str, None], stream.__aiter__())
		wait = first_wait
		try:
			while True:
				try:
					async with asyncio.timeout(wait):
						chunk = await iterator.__anext__()
				except StopAsyncIteration:
					return
				except TimeoutError as exc:
					raise StreamStalled(f"{label} sent nothing for {wait:g}s; the call was cancelled.") from exc
				wait = self.next_chunk_seconds
				yield chunk
		finally:
			await iterator.aclose()
