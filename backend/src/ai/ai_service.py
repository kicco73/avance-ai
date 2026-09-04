from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, AsyncIterator, Sequence, TYPE_CHECKING, overload

import partial_json_parser
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceProviderOutputTruncatedError,
	AIServiceRequestError,
	LLMProvider,
	MetadataCallback,
	ToolCallsRequested,
)
from ai._providers.cascading_llm_provider import AutoLiveLLMProvider, AutoTestLLMProvider
from ai._providers import gemini_provider_v2, openai_provider_v2, anthropic_provider_v2
from db import Db
from logging_factory import LoggerFactory

if TYPE_CHECKING:
	# Import guarded: tracking.sources -> ai.llm_provider (for ToolSpec)
	# would close a circular import if this were eager, since importing
	# anything under the `ai` package first runs ai/__init__.py, which
	# imports this module. Only needed for the annotations below, never
	# at runtime — same pattern as tracking.actuators.actuator_set's own
	# `if TYPE_CHECKING: from ai import AiService`.
	from tracking.sources import ToolSet

logger = LoggerFactory.get_logger(__name__)

# A tool-call round-trip (model asks for tools -> AiService resolves them
# -> model is called again with the results) per turn — well past any
# legitimate lookup chain; beyond this the model is almost certainly
# looping, so AiService gives up with a clear error rather than running
# away with API calls.
MAX_TOOL_ROUNDS = 5

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
		auto_config_indices: list[int] | None = None,
		db: Db | None = None,
	) -> None:
		self._auto_provider = auto_provider
		# Index-aligned with `configs`; both empty for a hand-built
		# AiService that only ever runs in auto mode.
		self._selectable_providers = selectable_providers or []
		self._configs = configs or []
		# Maps auto_provider's own internal pointer (an index into
		# whatever list it was actually built from) back to the matching
		# index in self._configs/_selectable_providers. Only diverges from
		# the identity mapping when for_live/for_test excluded a "no-auto"
		# entry from the cascade while keeping it in _configs (see their
		# own docstrings) — defaults to identity here so a hand-built
		# AiService (most tests, and any caller that never excludes
		# anything) needs no special handling.
		self._auto_config_indices = (
			auto_config_indices if auto_config_indices is not None else list(range(len(self._configs)))
		)
		# None = auto (use auto_provider); an index pins to that entry
		# of selectable_providers/configs instead.
		self._selected_index: int | None = None
		# get_input_tokens() cache, keyed by (provider label, prompt hash) —
		# the same prompt can cost a different count on a different
		# provider, so the label is part of the key, not just the hash.
		self._input_tokens_cache: LRUCache = LRUCache(maxsize=32)
		self._input_tokens_cache_lock = threading.Lock()
		# Optional: persists each call's input/output tokens for Manage
		# services' own daily consumption bar/trend chart (see
		# generate_stream_with_metadata's on_metadata tap and
		# db/ai_usage.py) — None in the many tests that build an AiService
		# by hand just to exercise the in-memory TokenCounter/cascade
		# logic, which stays entirely unaffected by this.
		self._db = db

	@classmethod
	def for_live(cls, ai_service_config: list[AIServiceConfig], db: Db | None = None) -> "AiService":
		"""Builds the live-chat cascade from only the entries whose own
		`modes` includes "live" (defaults to both live and test — see
		AIServiceConfig.modes) — entirely independent of for_test below:
		each classmethod filters the same incoming list on its own, builds
		its own fresh provider instances (_build_labeled_providers), and
		hands them to its own AutoLiveLLMProvider/AutoTestLLMProvider
		cascade. Nothing constructed here is shared with for_test's own
		result.

		An entry additionally tagged "no-auto" stays in live_config (and
		so in _configs/_selectable_providers — still manually pickable,
		still shown in get_models_info()'s "models") but is left out of
		the cascade auto_provider itself actually cycles through, via
		auto_config_indices — see _auto_eligible_indices."""
		live_config = cls._filter_by_mode(ai_service_config, "live")
		labeled = cls._build_labeled_providers(live_config)
		selectable = [AutoLiveLLMProvider([entry]) for entry in labeled]
		auto_config_indices = cls._auto_eligible_indices(live_config)
		auto_labeled = [labeled[i] for i in auto_config_indices]
		return cls(
			AutoLiveLLMProvider(auto_labeled), selectable_providers=selectable, configs=live_config,
			auto_config_indices=auto_config_indices, db=db,
		)

	@classmethod
	def for_test(cls, ai_service_config: list[AIServiceConfig], db: Db | None = None) -> "AiService":
		"""The test-panel/batch-run cascade — see for_live's own docstring
		for why this stays fully independent of it, and for what "no-auto"
		does here too."""
		test_config = cls._filter_by_mode(ai_service_config, "test")
		labeled = cls._build_labeled_providers(test_config)
		selectable = [AutoLiveLLMProvider([entry]) for entry in labeled]
		auto_config_indices = cls._auto_eligible_indices(test_config)
		auto_labeled = [labeled[i] for i in auto_config_indices]
		return cls(
			AutoTestLLMProvider(auto_labeled), selectable_providers=selectable, configs=test_config,
			auto_config_indices=auto_config_indices, db=db,
		)

	@staticmethod
	def _filter_by_mode(ai_service_config: list[AIServiceConfig], mode: str) -> list[AIServiceConfig]:
		return [service for service in ai_service_config if mode in service.modes]

	@staticmethod
	def _auto_eligible_indices(configs: list[AIServiceConfig]) -> list[int]:
		"""Indices into `configs` of every entry the auto cascade may
		actually land on — everything except one tagged "no-auto", which
		stays reachable only by an explicit select_model() pin (see
		for_live/for_test's own docstrings on why it's still in `configs`
		itself)."""
		return [i for i, service in enumerate(configs) if "no-auto" not in service.modes]

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
		auto_index = getattr(self._auto_provider, "current_index", 0)
		if 0 <= auto_index < len(self._auto_config_indices):
			return self._auto_config_indices[auto_index]
		return auto_index

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
		history: list[dict],
		tool_set: "ToolSet | None" = None,
	) -> str:
		chunks: list[str] = []
		async for chunk in self.generate_stream(system_prompt, history, tool_set=tool_set):
			chunks.append(chunk)
		return "".join(chunks)

	@overload
	async def prompt(self, prompt: str, channels: None = None, tool_set: "ToolSet | None" = None) -> str: ...
	@overload
	async def prompt(self, prompt: str, channels: list[str], tool_set: "ToolSet | None" = None) -> dict[str, str]: ...
	async def prompt(
		self, prompt: str, channels: list[str] | None = None, tool_set: "ToolSet | None" = None,
	) -> str | dict[str, str]:
		if not channels:
			return await self.generate("", [{"role": "user", "content": prompt}], tool_set=tool_set)
		schema = {"text": "Normal textual response, in markdown format, rendered as text."}
		schema.update({name: f"The requested '{name}', rendered as plain text." for name in channels})
		values: dict[str, str] = {}
		chunks: list[str] = []
		async for chunk in self.generate_stream_with_metadata(
			"", [{"role": "user", "content": prompt}],
			on_metadata=lambda name, value: values.__setitem__(name, str(value)),
			schema=schema,
			tool_set=tool_set,
		):
			chunks.append(chunk)
		values["text"] = "".join(chunks)
		return values

	def generate_stream(
		self,
		system_prompt: str,
		history: list[dict],
		tool_set: "ToolSet | None" = None,
	) -> AsyncIterator[str]:
		return self.generate_stream_with_metadata(
			system_prompt, history, on_metadata=lambda name, value: None,
			schema={"text": "Normal textual response, in markdown format, rendered as text."},
			tool_set=tool_set,
		)

	def is_provider_with_schema(self) -> bool:
		return isinstance(self._current_leaf_provider, LLMProvider)

	def _tap_token_usage(self, on_metadata: MetadataCallback, provider_label: str) -> MetadataCallback:
		"""Wraps `on_metadata` to also persist input_tokens/output_tokens
		(see each LLMProvider's own on_metadata calls) as one AiTokenUsage
		row once both have arrived — a no-op passthrough when this
		AiService wasn't built with a `db` (most tests). `provider_label`
		is the entry-time active provider, not re-read live off the
		cascade's own pointer: a *different* concurrent call through the
		same cascade could have already advanced that pointer past a
		failover by the time these events actually fire."""
		if self._db is None:
			return on_metadata
		db = self._db
		captured: dict[str, int] = {}

		def tap(name: str, value: Any) -> None:
			if name in ("input_tokens", "output_tokens"):
				captured[name] = value
				if "input_tokens" in captured and "output_tokens" in captured:
					db.record_ai_token_usage(provider_label, captured["input_tokens"], captured["output_tokens"])
			on_metadata(name, value)

		return tap

	async def generate_stream_with_metadata(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_metadata: MetadataCallback,
		schema: dict[str, str],
		tool_set: "ToolSet | None" = None,
	) -> AsyncIterator[str]:
		"""With no tool_set, this is exactly the single call it always was
		— same request, same live incremental parsing/yielding, byte for
		byte (see _stream_final_answer, unchanged from before tools
		existed). With one, the model may end a round asking for tools
		instead of answering: that round's own text (the model rarely
		produces any under a JSON-schema response, but nothing here
		assumes it doesn't) is drained and discarded, never yielded here —
		only the round that finally completes without a further
		ToolCallsRequested streams outward, so the partial-JSON parser
		below never has to reason about a tool-only interruption."""
		provider_label = self._current_provider_label
		tapped_on_metadata = self._tap_token_usage(on_metadata, provider_label)

		if tool_set is None:
			logger.info(f"generate_stream_with_metadata: provider={provider_label} fields={list(schema.keys())}")
			response_stream = self._active_provider.generate_stream_with_schema(
				system_prompt, history, schema=schema, on_metadata=tapped_on_metadata,
			) # type: ignore
			async for chunk in self._stream_final_answer(response_stream, schema, tapped_on_metadata, provider_label):
				yield chunk
			return

		# Extended with the assistant's own tool_calls message and one
		# 'tool' result message per call, round after round — local to
		# this turn only. Never written back to `history` (the caller's
		# own list) or persisted anywhere: TrackingProcessor never sees it,
		# and it's gone once this generator returns.
		turn_history = list(history)
		tool_specs = tool_set.specs()

		for round_number in range(1, MAX_TOOL_ROUNDS + 1):
			logger.info(
				f"generate_stream_with_metadata: provider={provider_label} fields={list(schema.keys())} "
				f"tool_round={round_number} tools={[spec.name for spec in tool_specs]}"
			)
			response_stream = self._active_provider.generate_stream_with_schema(
				system_prompt, turn_history, schema=schema, on_metadata=tapped_on_metadata, tools=tool_specs,
			) # type: ignore
			try:
				round_chunks = [chunk async for chunk in response_stream]
			except ToolCallsRequested as requested:
				turn_history.append({
					"role": "assistant", "tool_calls": requested.calls, "content": requested.assistant_content,
				})
				for call in requested.calls:
					# Sequential, never parallel — ToolSet.call's own
					# contract; each result must land in the history
					# before the next call runs, matching what a real
					# multi-step lookup actually depends on.
					result = await tool_set.call(call.name, call.arguments)
					turn_history.append({"role": "tool", "tool_call_id": call.id, "content": result})
				continue

			# The stream ended with no further tool request — the model's
			# real final answer, already fully collected above; replay it
			# through the exact same parser a live stream would use.
			async for chunk in self._stream_final_answer(
				self._as_async_iter(round_chunks), schema, tapped_on_metadata, provider_label,
			):
				yield chunk
			return

		raise AIServiceRequestError(
			f"Exceeded {MAX_TOOL_ROUNDS} tool-call rounds without a final response from the model."
		)

	@staticmethod
	async def _as_async_iter(items: list[str]) -> AsyncIterator[str]:
		for item in items:
			yield item

	async def _stream_final_answer(
		self,
		response_stream: AsyncIterator[str],
		schema: dict[str, str],
		on_metadata: MetadataCallback,
		provider_label: str,
	) -> AsyncIterator[str]:
		"""The model's own actual answer to `schema` — incremental
		partial-JSON parsing exactly as generate_stream_with_metadata
		always did it, before tool calls existed. `response_stream` is
		either the live provider call directly (no tool_set) or an
		already-fully-collected round's chunks replayed in order (a tool
		turn's own final round) — this method has no way to tell the two
		apart, and doesn't need to."""
		accumulated_json = ""
		emitted: set[str] = set()
		last_text_length = 0

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

		logger.info(f"generate_stream_with_metadata: stream ended normally, provider={provider_label} accumulated_json_length={len(accumulated_json)}")
		final_parsed = partial_json_parser.parse_json(accumulated_json)
		if not isinstance(final_parsed, dict) or not final_parsed:
			return
		last_inserted = next(reversed(final_parsed))
		if last_inserted != 'text' and last_inserted not in emitted:
			on_metadata(last_inserted, final_parsed[last_inserted])
