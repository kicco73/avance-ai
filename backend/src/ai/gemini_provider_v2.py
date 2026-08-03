from __future__ import annotations

from contextlib import contextmanager
from typing import Any, AsyncIterator, Generator
import logging

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
		# Bare default — no priority_tags/tags, just 'text' (see
		# build_schema's own docstring) — until whoever actually
		# constructs this (see ai.ai_service.AiService._build_provider)
		# wires up the real audio/signals/env contract externally, via
		# this same method, right after construction.
		self.build_schema({}, {})

	def build_schema(self, priority_tags: dict[str, tuple[type, str]], tags: dict[str, tuple[type, str]]) -> dict:
		"""Builds the structured-output schema Gemini's own response_schema
		expects, from a plain description of each field this provider wants
		back — generalized (not yet on the LLMProvider ABC, but written so
		it can move there later, provider-agnostic) so a future provider
		only ever needs to describe its own fields, never hand-write this
		shape itself. `priority_tags` are required, reported *before*
		'text' (e.g. "audio", so a TTS-worthy blurb is ready as early as
		possible in a streamed reply); `tags` are optional/nullable,
		reported *after* it (e.g. "signals"/"env", which only make sense
		once the reply itself is fully known). 'text' itself is never
		passed in — always inserted between the two, the one field every
		schema this builds needs. Every field is typed "STRING" regardless
		of its declared Python type — Gemini's own native OBJECT/ARRAY
		typing turned out unreliable at actually getting filled in (only
		"audio", a STRING, was ever reliably populated); the caller is
		responsible for treating a non-audio value as JSON-formatted text
		and parsing it itself (see chat.turn_strategy_v2.TurnStrategyV2.
		generate_reply's own handle_metadata)."""
		properties: dict[str, dict] = {}
		required: list[str] = []

		for name, (_python_type, description) in priority_tags.items():
			properties[name] = {
				"type": "STRING",
				"description": description,
			}
			required.append(name)

		properties["text"] = {
			"type": "STRING",
			"description": "Extended textual response for the user.",
		}
		required.append("text")

		for name, (_python_type, description) in tags.items():
			properties[name] = {
				"type": "STRING",
				"description": description,
				"nullable": False,
			}
			required.append(name)

		self._app_response_schema = {
			"type": "OBJECT",
			"properties": properties,
			"required": required,
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

		# Only the field *order* is spelled out here, generated straight
		# off this instance's own _field_order() rather than hardcoded —
		# a purely structural/mechanical fact this SDK-wiring class
		# already owns (see build_schema's own docstring: which fields
		# exist and their order is exactly what it configured), and
		# deliberately silent on what each field actually *means* or
		# *how* to fill it in — that's prompt content, still the calling
		# TurnStrategy's own job (see chat.turn_strategy_v2.
		# TurnStrategyV2._build_metadata_prompt). No [tag]-style bracket
		# wording either: response_schema/response_mime_type below are
		# what actually enforce the JSON *shape* — this is only a nudge
		# for the model to fill every field in a sensible order, not the
		# mechanism the shape itself depends on.
		field_order = self._field_order()
		order_instruction = (
			"Respond with the structured JSON object described by the response "
			"schema, filling in its fields in this order: "
			+ ", ".join(f"'{name}'" for name in field_order) + "."
		)
		full_system_prompt = f"{system_prompt}\n\n{order_instruction}"

		gen_config: types.GenerateContentConfig = types.GenerateContentConfig(
			system_instruction=full_system_prompt,
			max_output_tokens=MAX_OUTPUT_TOKENS,
			response_mime_type="application/json",
			response_schema=self._app_response_schema,
		)

		return contents, gen_config

	def _field_order(self) -> list[str]:
		# self._app_response_schema's own "properties" dict was built by
		# build_schema in exactly this order — priority_tags..., "text",
		# tags... (see its own docstring) — and a plain dict already
		# preserves insertion order, so this is the one place
		# generate_stream below reads "what fields exist, and in what
		# order" from, rather than hardcoding any of them by name.
		return list(self._app_response_schema["properties"].keys())

	async def generate_stream(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_retry: OnRetry | None = None,
		on_metadata: MetadataCallback | None = None,
	) -> AsyncIterator[str]:
		"""Yields only 'text' (the one field this class's own build_schema
		always makes required — see its docstring), streamed incrementally
		as it grows. Every other field this provider was configured to
		report (see build_schema's own priority_tags/tags) is delivered
		once each, generically, via on_metadata — a priority tag (ordered
		*before* 'text' in the schema's own properties) as soon as the
		next field in that same order has started appearing at all (since
		Gemini's structured output streams object properties strictly in
		schema order, that field's own value can't still be growing once
		the one after it exists); a tag ordered *after* 'text' the same
		way, using whichever field follows it — the very last field in the
		whole schema never gets a "next one" to signal its own completion
		this way, so it (and anything else still unseen) is instead
		flushed once the stream truly ends.
		"""

		contents, config = self._format_history_and_config(system_prompt, history)
		field_order = self._field_order()
		on_metadata = on_metadata or (lambda key, value: None)
		# `contents` is only the message history — the actual instructions
		# (base prompt + whichever TurnStrategy's own metadata section,
		# plus this method's own field-order nudge) live in
		# config.system_instruction instead, never in `contents` itself.

		with _handle_gemini_errors():
			response_stream = await self._client.aio.models.generate_content_stream(
				model=self._model_name,
				contents=contents,
				config=config,
			)

			accumulated_json = ""
			emitted: set[str] = set()
			emitted.add("text")  # 'text' is never reported via on_metadata, only yielded
			last_text_length = 0

			async for chunk in response_stream:
				if not chunk.text:
					continue

				accumulated_json += chunk.text

				try:
					parsed = partial_json_parser.parse_json(accumulated_json)
					if not isinstance(parsed, dict):
						continue

					for index, name in enumerate(field_order):
						if name == "text" or name in emitted or name not in parsed:
							continue
						next_name = field_order[index + 1] if index + 1 < len(field_order) else None
						if next_name is not None and next_name in parsed:
							on_metadata(name, parsed[name])
							emitted.add(name)

					if "text" in parsed:
						current_text = parsed["text"]
						if len(current_text) > last_text_length:
							delta = current_text[last_text_length:]
							last_text_length = len(current_text)
							yield delta

				except Exception:
					pass

			# The stream has truly ended — whatever never got a "next
			# field" of its own to signal completion (typically the last
			# field in schema order, see this method's own docstring) is
			# flushed here instead, same parse_json used for the still-
			# incremental parsing above (it already degrades gracefully
			# on trailing incomplete content, dropping it rather than
			# raising).
			try:
				final_parsed = partial_json_parser.parse_json(accumulated_json)
				if not isinstance(final_parsed, dict):
					return
				for key, value in final_parsed.items():
					if key not in emitted:
						on_metadata(key, value)
			except Exception:
				pass
