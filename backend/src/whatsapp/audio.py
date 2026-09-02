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

from talk.talk_format import PcmWavCodec

OPUS_SAMPLE_RATE = 48000
OPUS_BIT_RATE = 32000
WHATSAPP_VOICE_MIME = "audio/ogg; codecs=opus"


def split_wav(wav: bytes) -> tuple[bytes, int]:
    """(pcm, sample_rate) out of a WAV whose chunk sizes may be the
    streaming sentinel rather than real lengths — walks the chunks by
    name and takes everything after the data header as PCM."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Not a WAV stream.")
    offset, sample_rate = 12, PcmWavCodec.DEFAULT_SAMPLE_RATE
    while offset + 8 <= len(wav):
        chunk_id = wav[offset:offset + 4]
        size = struct.unpack("<I", wav[offset + 4:offset + 8])[0]
        if chunk_id == b"fmt ":
            _fmt, channels, sample_rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", wav[offset + 8:offset + 24])
            if channels != 1 or bits != 16:
                raise ValueError(f"Unsupported WAV layout: {channels} ch, {bits} bit (expected mono 16-bit).")
        elif chunk_id == b"data":
            return wav[offset + 8:], sample_rate
        if size == 0xFFFFFFFF:
            break
        offset += 8 + size + (size & 1)
    raise ValueError("WAV has no data chunk.")


def wav_to_ogg_opus(wav: bytes) -> bytes:
    """Complete OGG/Opus file (mono, 48 kHz, ~32 kbps) for a TalkService WAV."""
    import av  # local: only the voice path needs it, and only when configured

    pcm, sample_rate = split_wav(wav)
    if not pcm:
        raise ValueError("WAV has no audio samples.")
    source = av.open(io.BytesIO(PcmWavCodec.to_wav(pcm, sample_rate)))
    sink_buffer = io.BytesIO()
    sink = av.open(sink_buffer, "w", format="ogg")
    stream = sink.add_stream("libopus", rate=OPUS_SAMPLE_RATE, layout="mono")
    stream.bit_rate = OPUS_BIT_RATE
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_SAMPLE_RATE)
    try:
        for frame in source.decode(audio=0):
            for resampled in resampler.resample(frame):
                sink.mux(stream.encode(resampled))
        for resampled in resampler.resample(None):
            sink.mux(stream.encode(resampled))
        sink.mux(stream.encode(None))
    finally:
        sink.close()
        source.close()
    return sink_buffer.getvalue()
