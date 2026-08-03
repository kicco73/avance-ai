"""Abstract interface shared by all STT providers, mirroring
ai/llm_provider.py's LLMProvider — no cascade knowledge here: retry/
cascading is CascadingListenProvider's responsibility alone (see
cascading_listen_provider.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ListenProvider(ABC):
    @abstractmethod
    def transcribe(self, audio: bytes) -> str:
        """Returns the transcribed text for `audio`. Raises a
        cascade.ProviderError subclass on failure, never lets an
        unclassified exception escape."""
        raise NotImplementedError
