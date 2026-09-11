"""The audio (TTS) layer as a single, independent service. TalkService
also IS a TalkProvider: calling generate() looks like calling a single
provider, with retry/cascading, caching, and live-generation dedup hidden inside."""
from __future__ import annotations

import asyncio
import hashlib
from typing import AsyncIterator

from system.cascade import ProviderError
from system.logging_factory import LoggerFactory
from talk.config import TalkServiceConfig
from talk.talk_provider import TalkProvider
from talk.cascading_talk_provider import CascadingTalkProvider
from talk.gemini_talk_provider import GeminiTalkProvider
from talk.piper.piper_talk_provider import PiperTalkProvider
from talk.talk_store import TalkStore
from talk.talk_format import PcmWavCodec

logger = LoggerFactory.get_logger(__name__)


class TalkService(TalkProvider):
    """Thin wrapper around whatever TalkProvider it's given — a single
    concrete provider or a CascadingTalkProvider fronting several; the
    service itself doesn't care which."""

    _PROVIDER_CLASSES = {
        "gemini": GeminiTalkProvider,
        "piper": PiperTalkProvider,  # local, no `key` needed
    }

    def __init__(self, provider: TalkProvider) -> None:
        self._provider = provider
        self._store = TalkStore()
        self._warming: set[asyncio.Task] = set()

    @classmethod
    def from_config(cls, talk_service_config: list[TalkServiceConfig]) -> "TalkService":
        providers = [
            (f"{service.driver}/{service.model}", cls._build_provider(service))
            for service in talk_service_config
        ]
        return cls(CascadingTalkProvider(providers))

    @classmethod
    def _build_provider(cls, service: TalkServiceConfig) -> TalkProvider:
        if service.driver not in cls._PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid talk provider driver: {service.driver!r}. Must be one of: "
                f"{', '.join(cls._PROVIDER_CLASSES.keys())}"
            )
        return cls._PROVIDER_CLASSES[service.driver](api_key=service.key, model=service.model)

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def start(self, text: str) -> None:
        """Begins generating `text` now, into the store, so a consumer
        that asks later joins it from the first chunk instead of waiting
        for a generation that starts only when asked."""
        key = self._key(text)
        if self._store.get_live_generation(key) is not None or self._store.has(key):
            return
        live = self._store.start_live_generation(key)
        task = asyncio.create_task(self._drain(key, text, live))
        self._warming.add(task)
        task.add_done_callback(self._warming.discard)

    async def _drain(self, key: str, text: str, live) -> None:
        async for _ in self._produce(key, text, live):
            pass

    async def generate(self, text: str) -> AsyncIterator[bytes]:
        """WAV-framed bytes for `text`, content-addressed by a hash of
        `text` so a repeat request joins an in-flight generation or hits
        the cache. Never raises: a failure just ends the stream, logged."""
        key = self._key(text)

        live = self._store.get_live_generation(key)
        if live is not None:
            async for chunk in live.stream_from(0):
                yield chunk
            return

        cached = self._store.read_and_purge_older(key)
        if cached is not None:
            yield cached
            return

        async for chunk in self._produce(key, text, self._store.start_live_generation(key)):
            yield chunk

    async def _produce(self, key: str, text: str, live) -> AsyncIterator[bytes]:
        pcm_chunks: list[bytes] = []
        sample_rate = PcmWavCodec.DEFAULT_SAMPLE_RATE
        header_sent = False
        try:
            async for pcm_chunk, chunk_sample_rate in self._provider.generate(text):
                sample_rate = chunk_sample_rate
                pcm_chunks.append(pcm_chunk)
                if not header_sent:
                    header_sent = True
                    header = PcmWavCodec.streaming_header(sample_rate)
                    live.push(header)
                    yield header
                live.push(pcm_chunk)
                yield pcm_chunk
        except ProviderError as exc:
            logger.warning("Audio generation failed for %s: %s", key, exc)
        finally:
            live.finish()
            if pcm_chunks:
                self._store.save(key, PcmWavCodec.to_wav(b"".join(pcm_chunks), sample_rate))
            self._store.finish_live_generation(key)
