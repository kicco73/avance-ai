"""Audio provider backed by Google Gemini's text-to-speech API."""
from __future__ import annotations

from http import HTTPStatus
from typing import Iterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from talk.talk_format import PcmWavCodec
from talk.talk_provider import BufferedTalkProvider
from cascade import ProviderError, ProviderRateLimitedError, ProviderUnavailableError

TTS_VOICE = "kore"


def _audio_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
            )
        ),
    )


class GeminiTalkProvider(BufferedTalkProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def _synthesize(self, text: str) -> Iterator[tuple[bytes, int]]:
        """A plain, single-voice generate_content_stream call with
        response_modalities=["AUDIO"]. Yields raw PCM chunks as Gemini
        produces them, each paired with its sample rate."""
        try:
            stream = self._client.models.generate_content_stream(
                model=self._model,
                contents=text,
                config=_audio_config(),
            )
            for response in stream:
                if not response.candidates or not response.candidates[0].content.parts:
                    continue
                inline_data = response.candidates[0].content.parts[0].inline_data
                if inline_data is None or inline_data.data is None:
                    continue
                yield inline_data.data, PcmWavCodec.sample_rate_from_mime(inline_data.mime_type or "")
        except genai_errors.ClientError as exc:
            if exc.code == HTTPStatus.TOO_MANY_REQUESTS:
                raise ProviderRateLimitedError(
                    f"The Gemini TTS API rate limit was exceeded (status 429): {exc.message}"
                ) from exc
            raise ProviderError(
                f"Error from the Gemini TTS API (status {exc.code}): {exc.message}"
            ) from exc
        except genai_errors.ServerError as exc:
            if exc.code == HTTPStatus.SERVICE_UNAVAILABLE:
                raise ProviderUnavailableError(
                    "The Gemini TTS API is temporarily overloaded (status 503)."
                ) from exc
            raise ProviderError(
                f"Error from the Gemini TTS API (status {exc.code}). Please retry later."
            ) from exc
        except genai_errors.APIError as exc:
            raise ProviderError(f"Unexpected error from the Gemini TTS API: {exc}") from exc
