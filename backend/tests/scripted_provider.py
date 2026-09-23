"""An LLMProvider that does, on each call, exactly what the test scripted.

Each behaviour is one call's worth of stream. `Stalled` never sends a
byte; `RepliesAfter` sends the whole answer once that much time has
passed; `StallsAfter` sends the start of an answer and then nothing;
`Crashes` raises. Time is the loop's own (see virtual_clock.py), so a
stall costs the test nothing and a 13-minute reply is one `advance`.

`torn_down` counts the streams the caller closed before they finished —
a deadline that gave up on a call, as opposed to one that ran out.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import AsyncIterator

from ai.llm_provider import LLMProvider


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text

    async def chunks(self) -> AsyncIterator[str]:
        yield json.dumps({"text": self.text})


class RepliesAfter:
    def __init__(self, seconds: float, text: str) -> None:
        self.seconds = seconds
        self.text = text

    async def chunks(self) -> AsyncIterator[str]:
        await asyncio.sleep(self.seconds)
        yield json.dumps({"text": self.text})


class Stalled:
    async def chunks(self) -> AsyncIterator[str]:
        await asyncio.Event().wait()
        yield ""


class StallsAfter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    async def chunks(self) -> AsyncIterator[str]:
        yield json.dumps({"text": self.prefix})[:-2]
        await asyncio.Event().wait()


class Trickles:
    def __init__(self, words: list[str], gap: float) -> None:
        self.words = words
        self.gap = gap

    async def chunks(self) -> AsyncIterator[str]:
        yield '{"text": "'
        for word in self.words:
            await asyncio.sleep(self.gap)
            yield word
        yield '"}'


class Crashes:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def chunks(self) -> AsyncIterator[str]:
        raise self.error
        yield ""


class ScriptedProvider(LLMProvider):
    def __init__(self, *script) -> None:
        super().__init__()
        self._script = deque(script)
        self.started = 0
        self.finished = 0

    def then(self, *behaviours) -> "ScriptedProvider":
        self._script.extend(behaviours)
        return self

    @property
    def torn_down(self) -> int:
        return self.started - self.finished

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        self.started += 1
        assert self._script, f"call #{self.started} to the provider has no scripted behaviour"
        behaviour = self._script.popleft()
        async for chunk in behaviour.chunks():
            yield chunk
        self.finished += 1

    def get_input_tokens(self, prompt: str) -> int:
        return 0
