from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, AsyncIterator, Sequence

import partial_json_parser
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceProviderOutputTruncatedError,
	LLMProvider,
	MetadataCallback,
)
from ai.cascading_llm_provider import AutoLiveLLMProvider, AutoTestLLMProvider
from ai import gemini_provider_v2, openai_provider_v2, anthropic_provider_v2
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

class LRUCache(OrderedDict):
	def __init__(self, maxsize: int = 128) -> None:
		super().__init__()
		self.maxsize = maxsize

	def __getitem__(self, key):
		value = super().__getitem__(key)
		self.move_to_end(key)
		return value

	def __setitem__(self, key, value) -> None:
		if key in self:
			self.move_to_end(key)
		super().__setitem__(key, value)
		if len(self) > self.maxsize:
			self.popitem(last=False)


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
		# get_input_tokens() cache, keyed by (provider label, prompt hash) —
		# the same prompt can cost a different count on a different
		# provider, so the label is part of the key, not just the hash.
		self._input_tokens_cache: LRUCache = LRUCache(maxsize=32)
		self._input_tokens_cache_lock = threading.Lock()

	@classmethod
	def for_live(cls, ai_service_config: list[AIServiceConfig]) -> "AiService":
		"""Builds the live-chat cascade from only the entries whose own
		`modes` includes "live" (defaults to both live and test — see
		AIServiceConfig.modes) — entirely independent of for_test below:
		each classmethod filters the same incoming list on its own, builds
		its own fresh provider instances (_build_labeled_providers), and
		hands them to its own AutoLiveLLMProvider/AutoTestLLMProvider
		cascade. Nothing constructed here is shared with for_test's own
		result."""
		live_config = cls._filter_by_mode(ai_service_config, "live")
		labeled = cls._build_labeled_providers(live_config)
		selectable = [AutoLiveLLMProvider([entry]) for entry in labeled]
		return cls(AutoLiveLLMProvider(labeled), selectable_providers=selectable, configs=live_config)

	@classmethod
	def for_test(cls, ai_service_config: list[AIServiceConfig]) -> "AiService":
		"""The test-panel/batch-run cascade — see for_live's own docstring
		for why this stays fully independent of it."""
		test_config = cls._filter_by_mode(ai_service_config, "test")
		labeled = cls._build_labeled_providers(test_config)
		selectable = [AutoLiveLLMProvider([entry]) for entry in labeled]
		return cls(AutoTestLLMProvider(labeled), selectable_providers=selectable, configs=test_config)

	@staticmethod
	def _filter_by_mode(ai_service_config: list[AIServiceConfig], mode: str) -> list[AIServiceConfig]:
		return [service for service in ai_service_config if mode in service.modes]

	@classmethod
	def _build_labeled_providers(cls, ai_service_config: list[AIServiceConfig]) -> list[tuple[str, LLMProvider]]:
		return [
			(f"{service.driver}/{service.model}", cls._build_provider(service))
			for service in ai_service_config
		]

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
		"""The concrete provider a call would actually reach; unwraps an
		AutoLiveLLMProvider/AutoTestLLMProvider via getattr since
		_active_provider isn't guaranteed to be one."""
		return getattr(self._active_provider, "current_provider", self._active_provider)

	@property
	def _current_config_index(self) -> int:
		if self._selected_index is not None:
			return self._selected_index
		return getattr(self._auto_provider, "current_index", 0)

	@property
	def _current_provider_label(self) -> str:
		"""Identifies the concrete provider/model get_input_tokens() would
		actually hit right now — part of its cache key, since the same
		prompt can cost a different token count on a different provider."""
		index = self._current_config_index
		if 0 <= index < len(self._configs):
			config = self._configs[index]
			return f"{config.driver}/{config.model}"
		return type(self._current_leaf_provider).__name__

	def get_max_output_tokens(self) -> int:
		"""The active provider's configured output-token ceiling (see
		AIServiceConfig.max_output_tokens) — used by callers that need to
		size their own request to fit in one call, e.g. BatchSignalSource."""
		index = self._current_config_index
		if 0 <= index < len(self._configs):
			return self._configs[index].max_output_tokens
		return 4096

	def select_model(self, index: int | None) -> None:
		if index is not None and not (0 <= index < len(self._selectable_providers)):
			raise ValueError(f"Invalid model index: {index!r}.")
		self._selected_index = index

	def get_total_tokens(self) -> int:
		return self._active_provider.get_total_tokens()

	def get_input_tokens(self, prompt: str) -> int:
		cache_key = f"{self._current_provider_label}:{hashlib.sha256(prompt.encode()).hexdigest()}"
		with self._input_tokens_cache_lock:
			if cache_key in self._input_tokens_cache:
				return self._input_tokens_cache[cache_key]
		tokens = self._current_leaf_provider.get_input_tokens(prompt)
		with self._input_tokens_cache_lock:
			self._input_tokens_cache[cache_key] = tokens
		return tokens

	def get_models_info(self) -> dict:
		auto = self._selected_index is None
		current_index = self._current_config_index
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
		return self.generate_stream_with_metadata(
			system_prompt, history, on_metadata=lambda name, value: None,
			schema={"text": "Normal textual response, in markdown format, rendered as text."},
		)

	def is_provider_with_schema(self) -> bool:
		return isinstance(self._current_leaf_provider, LLMProvider)

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

		logger.info(f"generate_stream_with_metadata: provider={self._current_provider_label} fields={list(schema.keys())}")
		response_stream = self._active_provider.generate_stream_with_schema(system_prompt, history, schema=schema, on_metadata=on_metadata) # type: ignore

		try:
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
		except AIServiceProviderOutputTruncatedError as exc:
			# The trailing field (whichever key is still last in
			# accumulated_json) was cut off mid-value — unlike every other
			# field, it never got a chance to prove itself complete by being
			# superseded by a later key, so it can't be trusted. Every field
			# already emitted above is unaffected.
			logger.critical(f"{exc} -- discarding unterminated trailing field")
			if "text" not in schema:
				# Background metadata-only call (batch/turn-by-turn signal
				# extraction) — no user-visible text to lose, so a logged,
				# swallowed loss of the trailing field is the whole story.
				return
			# A schema with 'text' is always a live chat turn — the user's
			# own reply may be the very field that got cut short, so this
			# must surface as a visible error rather than end the stream
			# quietly (see chat/sse_turn.py's SseChatTurn._run).
			raise

		logger.info(f"generate_stream_with_metadata: stream ended normally, provider={self._current_provider_label} accumulated_json_length={len(accumulated_json)}")
		final_parsed = partial_json_parser.parse_json(accumulated_json)
		if not isinstance(final_parsed, dict) or not final_parsed:			
			return
		last_inserted = next(reversed(final_parsed))
		if last_inserted != 'text' and last_inserted not in emitted:
			on_metadata(last_inserted, final_parsed[last_inserted])
