"""LLM provider backed by the Google Gemini API."""
from __future__ import annotations

import logging
from typing import Iterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from audio_format import pcm_sample_rate
from ai.llm_provider import (
    LLMProvider,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 1024

# Gemini uses the roles "user"/"model", not "user"/"assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}

# Text-to-speech is a separate, dedicated model — the chat model configured
# via LLM_NAME (e.g. a fast/cheap model for ordinary replies) generally
# isn't itself audio-capable. Fixed here rather than configurable: this
# prototype only ever needs one voice, for one purpose.
TTS_MODEL = "gemini-2.5-flash-lite-preview-tts" #"gemini-2.5-flash-preview-tts"
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


def _content_to_text(content) -> str:
    """No prompt caching or `document` content blocks for Gemini in this
    version — main.py's provider-neutral 'attachment' blocks (see
    _build_priming_messages) are flattened into plain text instead, a
    reasonable fallback while every attachment is text. A PDF attachment
    (source type "base64") can't be represented as text and is skipped here:
    supporting it would mean building Gemini's `inline_data` parts with the
    raw base64 bytes, which is out of scope for now."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        source = block["source"]
        if source["type"] == "text":
            parts.append(f"[Attachment: {block['filename']}]\n{source['data']}")
        else:
            logger.warning(
                "Skipping unsupported binary attachment '%s' for Gemini (no document-block support yet).",
                block["filename"],
            )
    return "\n\n".join(parts)


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        contents = [
            {
                "role": _ROLE_MAP[message["role"]],
                "parts": [{"text": _content_to_text(message["content"])}],
            }
            for message in history
        ]

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise AIServiceProviderRateLimitedError(
                    f"The Gemini API rate limit was exceeded (status 429): {exc.message}"
                ) from exc
            raise AIServiceError(
                f"Error from the Gemini API (status {exc.code}): {exc.message}"
            ) from exc
        except genai_errors.ServerError as exc:
            if exc.code == 503:
                raise AIServiceProviderUnavailableError(
                    "The Gemini API is temporarily overloaded (status 503)."
                ) from exc
            raise AIServiceError(
                f"Error from the Gemini API (status {exc.code}). Please retry later."
            ) from exc
        except genai_errors.APIError as exc:
            raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc

        return response.text or ""

    def generate_audio_stream(self, text: str) -> Iterator[tuple[bytes, int]]:
        """Text-to-speech via a dedicated TTS model (see TTS_MODEL) — a
        plain, single-voice generate_content_stream call with
        response_modalities=["AUDIO"], not the chat model configured for
        generate() above. Yields raw PCM chunks as Gemini produces them,
        each paired with its sample rate (constant in practice, but read
        off every chunk rather than assumed). Any failure — including the
        model/API not actually supporting audio — just ends the
        iteration; whatever was already yielded stays valid, same
        tolerance as an unsupported provider returning nothing at all."""
        try:
            stream = self._client.models.generate_content_stream(
                model=TTS_MODEL,
                contents=text,
                config=_audio_config(),
            )
            for response in stream:
                if not response.candidates or not response.candidates[0].content.parts:
                    continue
                inline_data = response.candidates[0].content.parts[0].inline_data
                if inline_data is None or inline_data.data is None:
                    continue
                yield inline_data.data, pcm_sample_rate(inline_data.mime_type or "")
        except genai_errors.APIError as exc:
            logger.warning("Gemini audio streaming failed: %s", exc)
            return
