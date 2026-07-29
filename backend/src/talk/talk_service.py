"""The audio (TTS) layer as a single, independent service — same shape
as ai/ai_service.py's AiService, but TalkService also IS a
TalkProvider: from a caller's point of view, calling generate() looks
exactly like calling a single provider, retry/cascading, caching and
live-generation dedup all hidden inside. Owns the on-disk cache (see
talk_store.py/talk_format.py) — audio-subsystem internals, not
chat_service's concern.
"""
from __future__ import annotations

import hashlib
import logging
from typing import AsyncIterator

from config import TalkServiceConfig
from cascade import ProviderCascade, ProviderError, ProviderRateLimitedError, ProviderUnavailableError
from talk.talk_provider import TalkProvider
from talk.gemini_talk_provider import GeminiTalkProvider
from talk.piper.piper_talk_provider import PiperTalkProvider
from talk.talk_store import TalkStore
from talk.talk_format import DEFAULT_PCM_SAMPLE_RATE, pcm_to_wav, streaming_wav_header

logger = logging.getLogger(__name__)


class TalkServiceError(Exception):
    """Raised once every configured provider has failed — carries the
    last provider-specific error as __cause__, never leaks it directly."""


class TalkServiceNotAvailableError(Exception):
    """Raised by the controller when talk-service.enabled is false in
    .config.yml — there is no TalkService instance to call at all."""

    def __init__(self, message: str = "Audio generation is not enabled on this server.") -> None:
        super().__init__(message)


class TalkService(TalkProvider):
    _PROVIDER_CLASSES = {
        "gemini": GeminiTalkProvider,
        "piper": PiperTalkProvider,  # local, no `key` needed
    }

    def __init__(self, talk_service_config: list[TalkServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in talk_service_config
        ]
        self._cascade: ProviderCascade[TalkProvider] = ProviderCascade(providers, kind="talk")
        self._store = TalkStore()

    @classmethod
    def _build_provider(cls, service: TalkServiceConfig) -> TalkProvider:
        if service.name not in cls._PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid talk provider name: {service.name!r}. Must be one of: "
                f"{', '.join(cls._PROVIDER_CLASSES.keys())}"
            )
        return cls._PROVIDER_CLASSES[service.name](api_key=service.key, model=service.model)

    async def generate(self, text: str) -> AsyncIterator[bytes]:
        """WAV-framed bytes for `text`, streaming-compatible: content-
        addressed by a hash of `text` itself (no external id needed), so
        a repeat request joins an in-flight generation or hits the
        on-disk cache instead of re-synthesizing. Never raises: a
        failure just ends the stream, logged."""
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()

        live = self._store.get_live_generation(key)
        if live is not None:
            async for chunk in live.stream_from(0):
                yield chunk
            return

        cached = self._store.read_and_purge_older(key)
        if cached is not None:
            yield cached
            return

        live = self._store.start_live_generation(key)
        pcm_chunks: list[bytes] = []
        sample_rate = DEFAULT_PCM_SAMPLE_RATE
        header_sent = False
        try:
            async for pcm_chunk, chunk_sample_rate in self._generate(text):
                sample_rate = chunk_sample_rate
                pcm_chunks.append(pcm_chunk)
                if not header_sent:
                    header_sent = True
                    header = streaming_wav_header(sample_rate)
                    live.push(header)
                    yield header
                live.push(pcm_chunk)
                yield pcm_chunk
        except TalkServiceError as exc:
            logger.warning("Audio generation failed for %s: %s", key, exc)
        finally:
            live.finish()
            if pcm_chunks:
                self._store.save(key, pcm_to_wav(b"".join(pcm_chunks), sample_rate))
            self._store.finish_live_generation(key)

    async def _generate(self, text: str) -> AsyncIterator[tuple[bytes, int]]:
        """Raw (pcm_chunk, sample_rate) tuples for `text`, cascading across
        every configured provider. Reuses ProviderCascade.call_with_retry
        unchanged for the initial call; once a provider has already
        yielded some chunks, a failure can only cascade, not retry (see
        StreamingTalkProvider)."""
        last_error: BaseException | None = None
        for _ in range(len(self._cascade)):
            try:
                result = await self._cascade.call_with_retry(
                    lambda provider: provider.generate(text),
                    unavailable=ProviderUnavailableError,
                    rate_limited=ProviderRateLimitedError,
                )
            except (ProviderUnavailableError, ProviderRateLimitedError) as exc:
                raise TalkServiceError("Every configured audio provider failed.") from exc

            try:
                for chunk in result:
                    yield chunk
                return
            except ProviderError as exc:
                logger.warning("Audio provider failed mid-stream: %s", exc)
                last_error = exc
                self._cascade.advance()

        raise TalkServiceError("Every configured audio provider failed.") from last_error
