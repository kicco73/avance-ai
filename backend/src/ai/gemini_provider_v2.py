from __future__ import annotations

from contextlib import contextmanager
from typing import Any, AsyncIterator, Callable, Generator
import logging
import json

from google import genai
from google.genai import types
from google.genai.errors import APIError
import partial_json_parser

from cascade import OnRetry
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceError,
	AIServiceProviderRateLimitedError,
	AIServiceProviderUnavailableError,
	LLMProvider,
	MetadataCallback,
	content_to_text,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 1024

@contextmanager
def _handle_gemini_errors() -> Generator[None, None, None]:
	"""Context manager centralizzato per la gestione e rimappatura delle eccezioni dell'SDK Google GenAI."""
	try:
		yield
	except APIError as exc:
		code: int | None = getattr(exc, "code", None)
		message: str = getattr(exc, "message", str(exc))

		if code == 429:
			raise AIServiceProviderRateLimitedError(
				f"The Gemini API rate limit was exceeded (status 429): {message}"
			) from exc
		if code in (503, 504):
			raise AIServiceProviderUnavailableError(
				f"The Gemini service is temporarily overloaded (status {code}): {message}"
			) from exc
		raise AIServiceError(
			f"Error from the Gemini API (status {code}): {message}"
		) from exc
	except Exception as exc:
		raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc


class GeminiProvider(LLMProvider):
	def __init__(self, config: AIServiceConfig) -> None:
		self._client: genai.Client = genai.Client(
			api_key=config.key,
			http_options={"base_url": config.url} if config.url else None,
		)
		self._model_name: str = config.model

		# Schema JSON piatto per forzare l'ordine dei campi
		self._app_response_schema = {
			"type": "OBJECT",
			"properties": {
				"audio": {
					"type": "STRING",
					"description": "Short textual version for text-to-speech. Generated first.",
				},
				"text": {
					"type": "STRING",
					"description": "Extended textual response for the user, generated second.",
				},
				"signals": {
					"type": "OBJECT",
					"description": "JSON dictionary containing required calculated values.",
					"nullable": True,
				},
				"env": {
					"type": "OBJECT",
					"description": "Updated memory/environment state for the next turn.",
					"nullable": True,
				},
			},
			"required": ["audio", "text"],
		}

	def _format_history_and_config(
		self, system_prompt: str, history: list[dict[str, Any]]
	) -> tuple[list[types.Content], types.GenerateContentConfig]:
		contents: list[types.Content] = []

		for message in history:
			role: str = "model" if message["role"] == "assistant" else "user"
			text_content: str = content_to_text(message["content"], "Gemini")

			contents.append(
				types.Content(
					role=role,
					parts=[types.Part.from_text(text=text_content)],
				)
			)

		full_system_prompt = (
			f"{system_prompt}\n\n"
			"IMPORTANT: Respond in JSON format following this strict order:\n"
			"1. 'audio': short text for text-to-speech (generated first)\n"
			"2. 'text': extended textual response for the user\n"
			"3. 'signals': calculated parameters or metrics\n"
			"4. 'env': update to the memory/environment state for the next turn"
		)

		gen_config: types.GenerateContentConfig = types.GenerateContentConfig(
			system_instruction=full_system_prompt,
			max_output_tokens=MAX_OUTPUT_TOKENS,
			response_mime_type="application/json",
			response_schema=self._app_response_schema,
		)

		return contents, gen_config

	def generate(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_retry: OnRetry | None = None,
		on_metadata: MetadataCallback | None = None,
	) -> str:
		contents, config = self._format_history_and_config(system_prompt, history)

		with _handle_gemini_errors():
			response = self._client.models.generate_content(
				model=self._model_name,
				contents=contents,
				config=config,
			)
			raw_text = response.text or ""

			if not raw_text:
				return ""

			try:
				data = json.loads(raw_text)
				text_content = data.pop("text", "")

				# Invia tutti i metadati estratti alla callback se fornita
				if on_metadata:
					for key, val in data.items():
						if val is not None:
							on_metadata(key, val)

				return text_content
			except Exception:
				return raw_text

	async def generate_stream(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_retry: OnRetry | None = None,
		on_metadata: MetadataCallback | None = None,
	) -> AsyncIterator[str]:
		"""Restituisce un AsyncIterator[str] di solo testo per la UI.

		Se viene fornita `on_metadata`, la callback viene invocata appena
		un metadato ('audio', 'signals', 'env') viene completato.
		"""

		contents, config = self._format_history_and_config(system_prompt, history)

		with _handle_gemini_errors():
			response_stream = await self._client.aio.models.generate_content_stream(
				model=self._model_name,
				contents=contents,
				config=config,
			)

			accumulated_json = ""
			audio_emitted = False
			signals_emitted = False
			last_text_length = 0

			async for chunk in response_stream:
				if not chunk.text:
					continue

				accumulated_json += chunk.text

				try:
					parsed = partial_json_parser.parse_json(accumulated_json)
					if not isinstance(parsed, dict):
						continue

					# 1. AUDIO: Completo quando inizia 'text'
					if not audio_emitted and "audio" in parsed and "text" in parsed:
						audio_emitted = True
						if on_metadata:
							on_metadata("audio", parsed["audio"])

					# 2. TEXT: Notifica i soli frammenti per lo stream della UI
					if "text" in parsed:
						current_text = parsed["text"]
						print(f"DEBUG: text={current_text}")
						if len(current_text) > last_text_length:
							delta = current_text[last_text_length:]
							last_text_length = len(current_text)
							yield delta

					# 3. SIGNALS: Completo quando inizia 'env'
					if (
						not signals_emitted
						and "signals" in parsed
						and "env" in parsed
					):
						signals_emitted = True
						if on_metadata:
							on_metadata("signals", parsed["signals"])

				except Exception as exc:
					print(f"EXCEPTION: exc={exc}")
					pass

			# 4. CHIUSURA STREAM: Invia eventuali metadati rimasti a fine generazione
			try:
				final_parsed = partial_json_parser.ensure_json(accumulated_json)
				if isinstance(final_parsed, dict) and on_metadata:
					if not audio_emitted and "audio" in final_parsed:
						on_metadata("audio", final_parsed["audio"])

					if not signals_emitted and "signals" in final_parsed:
						on_metadata("signals", final_parsed["signals"])

					if "env" in final_parsed:
						on_metadata("env", final_parsed["env"])
			except Exception:
				pass
