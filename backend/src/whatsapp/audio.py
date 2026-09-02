"""WAV -> OGG/Opus for WhatsApp voice notes (see docs/WHATSAPP.md).

TalkService.generate yields WAV: PcmWavCodec.streaming_header (RIFF/data
sizes set to the 0xFFFFFFFF streaming sentinel) followed by raw 16-bit
mono PCM, or a complete cached WAV. WhatsApp only renders an audio as a
*voice note* (waveform, auto-play) when it's OGG/Opus; anything else is
shown as a file attachment. The encoder comes from PyAV, already in the
image as faster-whisper's own dependency — no ffmpeg binary needed.
"""
from __future__ import annotations

import io
import struct
from fractions import Fraction

from talk.talk_format import PcmWavCodec

OPUS_SAMPLE_RATE = 48000
OPUS_BIT_RATE = 32000
WHATSAPP_VOICE_MIME = "audio/ogg; codecs=opus"
_WAV_HEADER_MIN = 12


def _wav_pcm_start(wav: bytes) -> tuple[int, int] | None:
    """(offset of the first PCM byte, sample rate) once the header is
    complete, None while it isn't yet — chunk sizes may be the streaming
    sentinel rather than real lengths, so the chunks are walked by name."""
    offset, sample_rate = _WAV_HEADER_MIN, PcmWavCodec.DEFAULT_SAMPLE_RATE
    while offset + 8 <= len(wav):
        chunk_id = wav[offset:offset + 4]
        size = struct.unpack("<I", wav[offset + 4:offset + 8])[0]
        if chunk_id == b"fmt ":
            if offset + 24 > len(wav):
                return None
            _fmt, channels, sample_rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", wav[offset + 8:offset + 24])
            if channels != 1 or bits != 16:
                raise ValueError(f"Unsupported WAV layout: {channels} ch, {bits} bit (expected mono 16-bit).")
        elif chunk_id == b"data":
            return offset + 8, sample_rate
        if size == 0xFFFFFFFF:
            break
        offset += 8 + size + (size & 1)
    return None


def split_wav(wav: bytes) -> tuple[bytes, int]:
    """(pcm, sample_rate) out of a complete or streaming-framed WAV."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Not a WAV stream.")
    found = _wav_pcm_start(wav)
    if found is None:
        raise ValueError("WAV has no data chunk.")
    offset, sample_rate = found
    return wav[offset:], sample_rate


class OggOpusEncoder(object):
    """Incremental WAV -> OGG/Opus (mono, 48 kHz, ~32 kbps): push the
    TalkService stream piece by piece as it arrives, then finish() for the
    complete file. Not thread-safe: one pusher at a time."""

    def __init__(self) -> None:
        self._pending = b""
        self._sample_rate: int | None = None
        self._samples = 0
        self._sink_buffer = io.BytesIO()
        self._sink = None
        self._stream = None
        self._resampler = None

    @property
    def samples(self) -> int:
        return self._samples

    def push(self, wav_bytes: bytes) -> None:
        self._pending += wav_bytes
        if self._sample_rate is None:
            if len(self._pending) < _WAV_HEADER_MIN:
                return
            if self._pending[:4] != b"RIFF" or self._pending[8:12] != b"WAVE":
                raise ValueError("Not a WAV stream.")
            found = _wav_pcm_start(self._pending)
            if found is None:
                return
            offset, sample_rate = found
            self._open(sample_rate)
            self._pending = self._pending[offset:]
        whole = len(self._pending) - (len(self._pending) % 2)
        pcm, self._pending = self._pending[:whole], self._pending[whole:]
        self._encode(pcm)

    def finish(self) -> bytes:
        if self._sink is None:
            return b""
        try:
            for resampled in self._resampler.resample(None):
                self._sink.mux(self._stream.encode(resampled))
            self._sink.mux(self._stream.encode(None))
        finally:
            self._sink.close()
        return self._sink_buffer.getvalue()

    def _open(self, sample_rate: int) -> None:
        import av  # local: only the voice path needs it, and only when configured

        self._sample_rate = sample_rate
        self._sink = av.open(self._sink_buffer, "w", format="ogg")
        self._stream = self._sink.add_stream("libopus", rate=OPUS_SAMPLE_RATE, layout="mono")
        self._stream.bit_rate = OPUS_BIT_RATE
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_SAMPLE_RATE)

    def _encode(self, pcm: bytes) -> None:
        import av

        if not pcm:
            return
        assert self._sample_rate is not None and self._sink is not None
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.planes[0].update(pcm)
        frame.sample_rate = self._sample_rate
        frame.time_base = Fraction(1, self._sample_rate)
        frame.pts = self._samples
        self._samples += len(pcm) // 2
        for resampled in self._resampler.resample(frame):
            self._sink.mux(self._stream.encode(resampled))


def wav_to_ogg_opus(wav: bytes) -> bytes:
    """Complete OGG/Opus file for a whole TalkService WAV in one go."""
    encoder = OggOpusEncoder()
    encoder.push(wav)
    if encoder.samples == 0:
        raise ValueError("WAV has no audio samples.")
    return encoder.finish()
