"""STT provider backed by faster-whisper — fully local, CPU-only, no
network calls and no API key. Unlike Piper, it needs no manual model
file placement: WhisperModel(model) downloads and caches the model
itself on first use (see ~/.cache/huggingface/hub).
"""
from __future__ import annotations

import io

from faster_whisper import WhisperModel

from cascade import ProviderError
from listen.listen_provider import ListenProvider


class FasterWhisperProvider(ListenProvider):
    def __init__(self, api_key: str | None, model: str, language: str | None = None) -> None:
        # `api_key` accepted only to match every provider's uniform
        # (api_key, model) constructor shape; faster-whisper never uses it.
        self._model = WhisperModel(model, device="cpu", compute_type="int8")
        # None keeps faster-whisper's own autodetect; a real code skips it.
        self._language = language

    def transcribe(self, audio: bytes) -> str:
        try:
            segments, _info = self._model.transcribe(io.BytesIO(audio), language=self._language)
            return "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise ProviderError(f"faster-whisper transcription failed: {exc}") from exc
