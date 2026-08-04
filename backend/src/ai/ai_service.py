from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

import partial_json_parser
from ai.llm_provider import (
	LLMProvider,
	AIServiceConfig,
	LLMProviderWithSchema,
	MetadataCallback,
	PRIORITY_SCHEMA_TAGS,
	SCHEMA_TAGS,
)
from ai.cascading_llm_provider import CascadingLLMProvider
from ai.anthropic_provider import AnthropicProvider
from ai import gemini_provider, gemini_provider_v2
from ai import openai_provider, openai_provider_v2

_PROVIDER_CLASSES : dict[str, object] = {
	"anthropic": AnthropicProvider,
	"gemini-legacy": gemini_provider.GeminiProvider,
	"gemini": gemini_provider_v2.GeminiProvider,
	"openai-legacy": openai_provider.OpenAIProvider,
	"openai": openai_provider_v2.OpenAIProvider,
}
class AiService(object):

	def __init__(
		self,
		auto_provider: LLMProvider,
		selectable_providers: Sequence[LLMProvider] | None = None,
		configs: list[AIServiceConfig] | None = None,
	) -> None:
		self._auto_provider = auto_provider
		# Only present when built via from_config() below — index-aligned
		# with `configs`, and both empty for a hand-built AiService that
		# only ever runs in auto mode (select_model has nothing to pick).
		self._selectable_providers = selectable_providers or []
		self._configs = configs or []
		# None = auto (use auto_provider); an index pins to that entry of
		# selectable_providers/configs instead. In-memory only, like the
		# rest of this prototype's mutable state (e.g. auto_tracking_enabled).
		self._selected_index: int | None = None

	@classmethod
	def from_config(cls, ai_service_config: list[AIServiceConfig]) -> "AiService":
		labeled = [
			(f"{service.driver}/{service.model}", cls._build_provider(service))
			for service in ai_service_config
		]
		selectable = [CascadingLLMProvider([entry]) for entry in labeled]
		return cls(CascadingLLMProvider(labeled), selectable_providers=selectable, configs=ai_service_config)

	@staticmethod
	def _build_provider(service: AIServiceConfig) -> LLMProvider:
		if service.driver not in _PROVIDER_CLASSES:
			raise ValueError(
				f"Invalid provider driver: {service.driver!r}. Must be one of: "
				f"{', '.join(_PROVIDER_CLASSES.keys())}"
			)
		provider : LLMProvider = _PROVIDER_CLASSES[service.driver](service)
		if isinstance(provider, LLMProviderWithSchema):
			provider.build_schema(PRIORITY_SCHEMA_TAGS, SCHEMA_TAGS)

		return provider

	@property
	def _active_provider(self) -> LLMProvider:
		if self._selected_index is None:
			return self._auto_provider
		return self._selectable_providers[self._selected_index]

	@property
	def _current_leaf_provider(self) -> LLMProvider:
		"""The concrete provider a call would actually reach right now —
		_active_provider is typically a CascadingLLMProvider (see its own
		current_provider), but this class's own contract only ever
		promises a plain LLMProvider (see this class's own docstring), so
		a hand-built instance wrapping a bare leaf directly is also legal
		— falls back to _active_provider itself in that case, same
		defensive getattr as get_models_info's own current_index lookup
		just below."""
		return getattr(self._active_provider, "current_provider", self._active_provider)

	def select_model(self, index: int | None) -> None:
		"""`index=None` selects auto (the cascade's own retry/fallback
		order); an int pins generate()/generate_stream() to that single
		configured model directly. Raises ValueError for an out-of-range
		index."""
		if index is not None and not (0 <= index < len(self._selectable_providers)):
			raise ValueError(f"Invalid model index: {index!r}.")
		self._selected_index = index

	def get_models_info(self) -> dict:
		auto = self._selected_index is None
		current_index = (
			getattr(self._auto_provider, "current_index", 0) if auto else self._selected_index
		)
		return {
			"auto": auto,
			"current_index": current_index,
			"models": [
				{
					"driver": c.driver,
					"model": c.model,
					"url": c.url,
					"ui_label": c.ui_label,
					"ui_description": c.ui_description,
				}
				for c in self._configs
			],
		}

	async def generate(
		self,
		system_prompt: str,
		history: list[dict]
	) -> str:
		return await self._active_provider.generate(system_prompt, history)

	def generate_stream(
		self,
		system_prompt: str,
		history: list[dict],
	) -> AsyncIterator[str]:
		return self._active_provider.generate_stream(system_prompt, history)

	def supports_schema(self) -> bool:
		return isinstance(self._current_leaf_provider, LLMProviderWithSchema)

	async def generate_stream_with_metadata(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_metadata: MetadataCallback
	) -> AsyncIterator[str]:

		accumulated_json = ""
		emitted: set[str] = set()
		last_text_length = 0


		response_stream = self._active_provider.generate_stream(system_prompt, history)

		async for chunk in response_stream:
			accumulated_json += chunk
			parsed = partial_json_parser.parse_json(accumulated_json)
			if not isinstance(parsed, dict):
				continue

			if len(parsed):
				emitting = set(parsed.keys()) - emitted
				potentially_incomplete = next(reversed(parsed))
				completed = emitting - {potentially_incomplete}

				for name in completed:
					if name != "text":
						print("SENDING", name, parsed[name])
						on_metadata(name, parsed[name])
						emitted.add(name)

			if "text" in parsed:
				current_text = str(parsed["text"])

				if len(current_text) > last_text_length:
					delta = current_text[last_text_length:]
					last_text_length = len(current_text)
					yield delta

		final_parsed = partial_json_parser.parse_json(accumulated_json)
		if not isinstance(final_parsed, dict) or not final_parsed:			
			return
		last_inserted = next(reversed(final_parsed))
		if last_inserted != 'text' and last_inserted not in emitted:
			on_metadata(last_inserted, final_parsed[last_inserted]) 
			print("EN FIN", last_inserted, final_parsed[last_inserted])
		print("JSON", final_parsed)
