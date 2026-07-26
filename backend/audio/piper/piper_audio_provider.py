"""Audio provider backed by Piper — fully local/offline neural TTS, no
network calls, no API key. Voice model files (a `.onnx` model plus its
`.onnx.json` config) are placed by hand in audio/piper/models/, not
downloaded or managed by this module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from piper import PiperVoice

from audio.audio_provider import StreamingAudioProvider, AudioProviderError


class PiperAudioProvider(StreamingAudioProvider):
    # Where voice model files are expected — this directory, not read from
    # .config.yml (only the voice *name* is, via `model`; see AudioService).
    VOICES_DIR = Path(__file__).resolve().parent / 'models'

    def __init__(self, api_key: str | None, model: str) -> None:
        # `model` is the voice name (e.g. "ca_ES-upc_ona-medium"), not an
        # API key — api_key is accepted only to match every provider's
        # uniform (api_key, model) constructor shape (see
        # AudioService._build_provider); Piper never uses it.
        model_path = self.VOICES_DIR / f"{model}.onnx"
        config_path = self.VOICES_DIR / f"{model}.onnx.json"
        if not model_path.is_file() or not config_path.is_file():
            raise FileNotFoundError(
                f"Piper voice '{model}' not found. Expected both {model_path} "
                f"and {config_path} to exist — place the downloaded Piper "
                f"voice files there by hand."
            )
        self._voice = PiperVoice.load(model_path, config_path=config_path)

    def generate_audio(self, text: str) -> Iterator[tuple[bytes, int]]:
        try:
            for chunk in self._voice.synthesize(text):
                yield chunk.audio_int16_bytes, chunk.sample_rate
        except Exception as exc:
            raise AudioProviderError(f"Piper synthesis failed: {exc}") from exc
