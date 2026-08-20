"""Abstract interface shared by all audio (TTS) providers. No cascade
knowledge here: retry/cascading is CascadingTalkProvider's responsibility alone."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class TalkProvider(ABC):
    @abstractmethod
    def generate(self, text: str) -> Iterator[tuple[bytes, int]]:
        """Yields (raw_pcm_chunk, sample_rate) tuples for `text`."""
        raise NotImplementedError


class StreamingTalkProvider(TalkProvider):
    """Forwards chunks as _synthesize() produces them, no buffering — for
    a provider with nothing worth retrying mid-stream (e.g. local Piper)."""

    def generate(self, text: str) -> Iterator[tuple[bytes, int]]:
        yield from self._synthesize(text)

    @abstractmethod
    def _synthesize(self, text: str) -> Iterator[tuple[bytes, int]]:
        raise NotImplementedError


class BufferedTalkProvider(TalkProvider):
    """Materializes the whole utterance before returning any of it — for
    a provider whose failures are worth retrying (e.g. a remote API)."""

    def generate(self, text: str) -> list[tuple[bytes, int]]:
        return list(self._synthesize(text))

    @abstractmethod
    def _synthesize(self, text: str) -> Iterator[tuple[bytes, int]]:
        raise NotImplementedError
