"""The spoken half of a reply, as bytes WhatsApp will take.

A reply's [audio] text is announced by the turn well before the rest of
the reply is written (`output.speech`, see whatsapp/turn_exchange.py);
synthesis starts right then, so by the time the note is actually wanted
it is already encoded. The synthesis and the encoding are one pass: every
WAV piece AiTalker.talk() yields goes straight into the encoder.

A prefetch is an optimisation and never a dependency — mp3_for
synthesizes on the spot when nothing was started for that text.
"""
from __future__ import annotations

import asyncio

from system.logging_factory import LoggerFactory
from talker import AiTalker

logger = LoggerFactory.get_logger(__name__)


class VoiceNoteSynthesizer(object):
    _MAX_PENDING = 16

    def __init__(self, ai_talker: AiTalker) -> None:
        self._ai_talker = ai_talker
        self._pending: dict[str, asyncio.Task[bytes]] = {}

    def on_metadata(self, key: str, value) -> None:
        if key != "audio" or not value or value in self._pending:
            return
        while len(self._pending) >= self._MAX_PENDING:
            self._pending.pop(next(iter(self._pending))).cancel()
        task = asyncio.create_task(self._synthesize(value))
        task.add_done_callback(_log_unretrieved_failure)
        self._pending[value] = task

    async def mp3_for(self, text: str) -> bytes:
        started = self._pending.pop(text, None)
        if started is not None:
            return await started
        return await self._synthesize(text)

    def cancel(self) -> None:
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()

    async def _synthesize(self, text: str) -> bytes:
        from whatsapp.audio import Mp3Encoder

        encoder = Mp3Encoder()
        async for wav_piece in self._ai_talker.talk(text):
            await asyncio.to_thread(encoder.push, wav_piece)
        return await asyncio.to_thread(encoder.finish)


def _log_unretrieved_failure(task: "asyncio.Task[bytes]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"WhatsApp: voice note synthesis started ahead of the reply failed: {exc}")
