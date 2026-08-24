from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

import partial_json_parser
from ai.llm_provider import (
	LLMProvider,
	AIServiceConfig,
	LLMProviderWithSchema,
	MetadataCallback,
)
from ai.cascading_llm_provider import CascadingLLMProvider
from ai import gemini_provider_v2, openai_provider_v2, anthropic_provider_v2

_PROVIDER_CLASSES : dict[str, object] = {
	"anthropic": anthropic_provider_v2.AnthropicProvider,
	"gemini": gemini_provider_v2.GeminiProvider,
	"openai": openai_provider_v2.OpenAICompatibleProvider,
	"llama.cpp": openai_provider_v2.OpenAICompatibleProvider,
}
class AiService(object):

	def __init__(
		self,
		auto_provider: LLMProvider,
		selectable_providers: Sequence[LLMProvider] | None = None,
		configs: list[AIServiceConfig] | None = None,
	) -> None:
		self._auto_provider = auto_provider
		# Index-aligned with `configs`; both empty for a hand-built
		# AiService that only ever runs in auto mode.
		self._selectable_providers = selectable_providers or []
		self._configs = configs or []
		# None = auto (use auto_provider); an index pins to that entry
		# of selectable_providers/configs instead.
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
		provider : LLMProvider = _PROVIDER_CLASSES[service.driver](service) # type: ignore
		return provider

	@property
	def _active_provider(self) -> LLMProvider:
		if self._selected_index is None:
			return self._auto_provider
		return self._selectable_providers[self._selected_index]

	@property
	def _current_leaf_provider(self) -> LLMProvider:
		"""The concrete provider a call would actually reach; unwraps a
		CascadingLLMProvider via getattr since _active_provider isn't
		guaranteed to be one."""
		return getattr(self._active_provider, "current_provider", self._active_provider)

	def select_model(self, index: int | None) -> None:
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
		chunks: list[str] = []
		async for chunk in self.generate_stream(system_prompt, history):
			chunks.append(chunk)
		return "".join(chunks)

	def generate_stream(
		self,
		system_prompt: str,
		history: list[dict],
	) -> AsyncIterator[str]:
		if self.is_provider_with_schema():
			return self.generate_stream_with_metadata(
				system_prompt, history, on_metadata=lambda name, value: None,
				schema={"text": "Normal textual response, in markdown format, rendered as text."},
			)
		return self._active_provider.generate_stream(system_prompt, history)

	def is_provider_with_schema(self) -> bool:
		return isinstance(self._current_leaf_provider, LLMProviderWithSchema)

	async def generate_stream_with_metadata(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_metadata: MetadataCallback,
		schema: dict[str, str]
	) -> AsyncIterator[str]:

		accumulated_json = ""
		emitted: set[str] = set()
		last_text_length = 0

		response_stream = self._active_provider.generate_stream_with_schema(system_prompt, history, schema=schema) # type: ignore

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
