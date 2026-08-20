"""Raw-PCM <-> WAV helpers. TTS output is always raw 16-bit mono PCM,
never a self-describing container — this is the one place that gets
turned into something a browser can actually play."""
from __future__ import annotations

import io
import struct
import wave

DEFAULT_PCM_SAMPLE_RATE = 24000  # Gemini TTS's documented output rate.


def pcm_sample_rate(mime_type: str) -> int:
    """Gemini's inline_data.mime_type for TTS output looks like
    'audio/L16;codec=pcm;rate=24000' — pull the rate out of it, falling
    back to the documented default if the field is ever missing."""
    for segment in mime_type.split(";"):
        segment = segment.strip()
        if segment.startswith("rate="):
            return int(segment.removeprefix("rate="))
    return DEFAULT_PCM_SAMPLE_RATE


def pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
    """A complete, correctly-sized WAV file for `pcm_data` — used once all
    of it is known: the non-streaming path, and the on-disk copy written
    once a streamed generation finishes (see ChatService)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return buffer.getvalue()


def streaming_wav_header(sample_rate: int) -> bytes:
    """A WAV header for when the total length isn't known yet — the RIFF
    and data chunk sizes are 0xFFFFFFFF, the conventional sentinel for
    "streaming, more to come" so a client can start playing progressively."""
    channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return b"".join([
        b"RIFF", struct.pack("<I", 0xFFFFFFFF), b"WAVE",
        b"fmt ", struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample),
        b"data", struct.pack("<I", 0xFFFFFFFF),
    ])
