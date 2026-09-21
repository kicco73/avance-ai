from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from http import HTTPStatus
from typing import Any, AsyncIterator, Sequence, TYPE_CHECKING, overload

import partial_json_parser
from turn.errors import TurnServiceError
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceProviderOutputTruncatedError,
	LLMProvider,
	MetadataCallback,
	SystemPrompt,
	ToolCallsRequested,
	content_to_text,
)
from ai._providers.cascading_llm_provider import AutoLiveLLMProvider, AutoTestLLMProvider
from ai.stream_deadline import StreamDeadline, StreamStalled
from ai._providers import gemini_provider_v2, openai_provider_v2, anthropic_provider_v2
from db import Db
from system.web_session import WebSession
from token_estimate import estimate_tokens
from system.logging_factory import LoggerFactory

if TYPE_CHECKING:
	from tracking.sources import ToolSet

logger = LoggerFactory.get_logger(__name__)
MAX_TOOL_ROUNDS = 3
_TOOL_ERROR_DIRECTIVE = (
	" This call failed — no data was returned. Tell the user the lookup could not be completed; "
	"never invent data to fill the gap."
)


def estimate_history_tokens(turn_history: list[dict[str, Any]]) -> int:
	"""turn_history's own rough size, for _enforce_input_budget — content_to_text
	alone can't handle every shape a tool-calling round appends there: an
	assistant tool-call message's own {"role": "assistant", "tool_calls":
	[...], "content": ...} can carry None as `content` (nothing said
	before the call) or a provider-specific opaque replay payload (e.g.
	Gemini's own {"gemini_parts": [...]}) — content_to_text has no
	meaningful text for either shape and raises trying to iterate them.
	What actually counts toward size there is the call arguments
	themselves, since those are what round-trips back to the model on the
	next round; a replay payload is opaque parts, not text, and counts as
	nothing. A 'tool' result message's own `content` is always a plain
	string (see ToolSet.call, which never raises). Every other message
	(str or list-of-blocks content) is exactly what content_to_text
	already handles, unchanged."""
	pieces: list[str] = []
	for message in turn_history:
		content = message.get("content")
		if message.get("role") == "assistant" and "tool_calls" in message:
			if isinstance(content, str):
				pieces.append(content)
			pieces.extend(json.dumps(call.arguments) for call in message["tool_calls"])
		elif message.get("role") == "tool":
			pieces.append(content if isinstance(content, str) else str(content))
		else:
			pieces.append(content_to_text(content))
	return estimate_tokens("\n".join(pieces))

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
		input_token_budget_per_turn: int | None = None,
		deadline: StreamDeadline | None = None,
	) -> None:
		self._auto_provider = auto_provider
		self._deadline = deadline if deadline is not None else StreamDeadline()
		self._selectable_providers = selectable_providers or []
		self._configs = configs or []
		self._auto_config_indices = (
			auto_config_indices if auto_config_indices is not None else list(range(len(self._configs)))
		)
		self._selected_index: int | None = None
		self._input_tokens_cache: LRUCache = LRUCache(maxsize=32)
		self._input_tokens_cache_lock = threading.Lock()
		self._db = db
		self._input_token_budget_per_turn = input_token_budget_per_turn

	@classmethod
	def for_live(
		cls, ai_service_config: list[AIServiceConfig], db: Db | None = None,
		input_token_budget_per_turn: int | None = None,
	) -> "AiService":
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
		return cls._for_mode(
			ai_service_config, "live", AutoLiveLLMProvider, db, input_token_budget_per_turn,
		)

	@classmethod
	def for_test(
		cls, ai_service_config: list[AIServiceConfig], db: Db | None = None,
		input_token_budget_per_turn: int | None = None,
	) -> "AiService":
		"""The test-panel/batch-run cascade — see for_live's own docstring
		for why this stays fully independent of it, and for what "no-auto"
		does here too."""
		return cls._for_mode(
			ai_service_config, "test", AutoTestLLMProvider, db, input_token_budget_per_turn,
		)

	@classmethod
	def _for_mode(
		cls, ai_service_config: list[AIServiceConfig], mode: str,
		auto_provider_class: type[AutoLiveLLMProvider], db: Db | None,
		input_token_budget_per_turn: int | None,
	) -> "AiService":
		mode_config = cls._filter_by_mode(ai_service_config, mode)
		labeled = cls._build_labeled_providers(mode_config)
		selectable = [AutoLiveLLMProvider([entry]) for entry in labeled]
		auto_config_indices = cls._auto_eligible_indices(mode_config)
		auto_labeled = [labeled[i] for i in auto_config_indices]
		return cls(
			auto_provider_class(auto_labeled), selectable_providers=selectable, configs=mode_config,
			auto_config_indices=auto_config_indices, db=db,
			input_token_budget_per_turn=input_token_budget_per_turn,
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

	def _current_config(self) -> AIServiceConfig | None:
		index = self._current_config_index
		if 0 <= index < len(self._configs):
			return self._configs[index]
		return None

	@property
	def _current_provider_label(self) -> str:
		"""Identifies the concrete provider/model get_input_tokens() would
		actually hit right now — part of its cache key, since the same
		prompt can cost a different token count on a different provider."""
		config = self._current_config()
		if config is None:
			return type(self._current_leaf_provider).__name__
		return f"{config.driver}/{config.model}"

	def get_max_output_tokens(self) -> int:
		"""The active provider's configured output-token ceiling (see
		AIServiceConfig.max_output_tokens) — used by callers that need to
		size their own request to fit in one call, e.g. BatchSignalSource."""
		config = self._current_config()
		if config is None:
			return 4096
		return config.max_output_tokens

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

	def get_models_snapshot(self) -> dict:
		"""What identifies the models a result was produced with, and
		nothing else. Kept apart from get_models_info() because this one
		is written down and kept, once per recorded result: the words a
		page shows a human are not part of what produced an answer."""
		return {
			"auto": self._selected_index is None,
			"current_index": self._current_config_index,
			"models": [
				{"driver": c.driver, "model": c.model, "url": c.url}
				for c in self._configs
			],
		}

	def get_models_info(self) -> dict:
		info = self.get_models_snapshot()
		for model, config in zip(info["models"], self._configs):
			model["ui_label"] = config.ui_label
			model["ui_description"] = config.ui_description
		return info

	async def generate(
		self,
		system_prompt: "str | SystemPrompt",
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
		system_prompt: "str | SystemPrompt",
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
		"""Wraps `on_metadata` to also persist input_tokens/output_tokens/
		cache_read_tokens/cache_creation_tokens (see each LLMProvider's own
		on_metadata calls) as one AiTokenUsage row once both input_tokens
		and output_tokens have arrived — a no-op passthrough when this
		AiService wasn't built with a `db` (most tests). `provider_label`
		is the entry-time active provider, not re-read live off the
		cascade's own pointer: a *different* concurrent call through the
		same cascade could have already advanced that pointer past a
		failover by the time these events actually fire. `captured` is
		reset right after each write, not just left to accumulate: a
		tool-calling turn fires this same tap once per round (see
		generate_stream_with_metadata's own loop below), and without the
		reset, round 2's own input_tokens would pair with round 1's still-
		cached output_tokens for one spurious extra row before round 2's
		own output_tokens overwrites it — one real row per round, not one
		real plus one wrong. Every provider emits the two cache events
		before input_tokens/output_tokens (see each one's own
		generate_stream_with_schema), so both are already in `captured`
		by the time the pair completes and the row is written; a provider
		that emits neither defaults both to 0 here rather than never
		writing the row at all."""
		if self._db is None:
			return on_metadata
		db = self._db
		captured: dict[str, int] = {}

		def tap(name: str, value: Any) -> None:
			if name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
				captured[name] = value
				if "input_tokens" in captured and "output_tokens" in captured:
					db.record_ai_token_usage(
						provider_label, captured["input_tokens"], captured["output_tokens"],
						captured.get("cache_read_tokens", 0), captured.get("cache_creation_tokens", 0),
					)
					captured.clear()
			on_metadata(name, value)

		return tap

	def _enforce_input_budget(
		self, system_prompt: "str | SystemPrompt", turn_history: list[dict[str, Any]], tool_set: "ToolSet",
		tool_call_records: list[dict[str, Any]], round_number: int,
	) -> None:
		if self._input_token_budget_per_turn is None:
			return
		total = estimate_tokens(SystemPrompt.coerce(system_prompt).full_text()) + estimate_history_tokens(turn_history)
		if total <= self._input_token_budget_per_turn:
			return
		message = (
			f"This request's own estimated size (~{total} tokens, including accumulated tool results) is "
			f"over the {self._input_token_budget_per_turn}-token input-token-budget-per-turn cap."
		)
		if self._db is not None:
			def _entry_tokens(record: dict[str, Any]) -> int:
				return estimate_tokens(json.dumps(record["arguments"])) + estimate_tokens(record["result"])
			heaviest = sorted(tool_call_records, key=_entry_tokens, reverse=True)[:3]
			heaviest_text = "; ".join(
				f"{record['name']}({record['arguments']}) ~{_entry_tokens(record)} tok" for record in heaviest
			)
			warning_message = (
				f"{message} Session {tool_set.session_id} (project '{tool_set.project_id}'), round "
				f"{round_number}, ~{total} tokens total. Heaviest accumulated tool result(s): {heaviest_text}."
			)
			logger.warning(warning_message)
			self._db.save_system_warning(WebSession().user, tool_set.project_id, "input_budget_exceeded", warning_message)
		raise TurnServiceError(
			message, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, code="input_budget_exceeded",
		)

	async def generate_stream_with_metadata(
		self,
		system_prompt: "str | SystemPrompt",
		history: list[dict[str, Any]],
		on_metadata: MetadataCallback,
		schema: dict[str, str],
		tool_set: "ToolSet | None" = None,
		force_required_tools: bool = False,
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
			response_stream = self._deadline.streaming(self._active_provider.generate_stream_with_schema(
				system_prompt, history, schema=schema, on_metadata=tapped_on_metadata,
			), provider_label) # type: ignore
			async for chunk in self._stream_final_answer(response_stream, schema, tapped_on_metadata, provider_label):
				yield chunk
			return
		turn_history = list(history)
		tool_specs = tool_set.specs()
		required_specs = tool_set.required_specs()
		input_tokens_by_round: list[int] = []
		cache_read_tokens_by_round: list[int] = []
		cache_creation_tokens_by_round: list[int] = []
		tool_call_records: list[dict[str, Any]] = []

		def _tally_input_tokens(name: str, value: Any) -> None:
			if name == "input_tokens":
				input_tokens_by_round.append(value)
			elif name == "cache_read_tokens":
				cache_read_tokens_by_round.append(value)
			elif name == "cache_creation_tokens":
				cache_creation_tokens_by_round.append(value)
			tapped_on_metadata(name, value)

		for round_number in range(1, MAX_TOOL_ROUNDS + 1):
			self._enforce_input_budget(system_prompt, turn_history, tool_set, tool_call_records, round_number)
			required_this_round = required_specs if (round_number == 1 and force_required_tools and required_specs) else None
			logger.info(
				f"generate_stream_with_metadata: provider={provider_label} fields={list(schema.keys())} "
				f"tool_round={round_number} tools={[spec.name for spec in tool_specs]} "
				f"required_tools={[spec.name for spec in required_this_round] if required_this_round else []}"
			)
			response_stream = self._deadline.tool_round(self._active_provider.generate_stream_with_schema(
				system_prompt, turn_history, schema=schema, on_metadata=_tally_input_tokens, tools=tool_specs,
				tool_round=round_number, required_tools=required_this_round,
			), provider_label) # type: ignore
			try:
				round_chunks = [chunk async for chunk in self._within_deadline(response_stream)]
			except ToolCallsRequested as requested:
				turn_history.append({
					"role": "assistant", "tool_calls": requested.calls, "content": requested.assistant_content,
				})
				for call in requested.calls:
					tapped_on_metadata("tool", tool_set.tool_event(call.name, call.arguments, "start", round=round_number))
					call_started = time.monotonic()
					result = await tool_set.call(call.name, call.arguments)
					elapsed_ms = round((time.monotonic() - call_started) * 1000)
					event = tool_set.tool_event(
						call.name, call.arguments, "result", round=round_number, result=result, duration_ms=elapsed_ms,
					)
					logger.info(
						f"tool call: session={tool_set.session_id} round={event['round']} name={event['name']} "
						f"arguments={event['arguments']} result_chars={len(event['result'])} duration_ms={event['duration_ms']}"
					)
					tapped_on_metadata("tool", event)
					model_facing_result = result + _TOOL_ERROR_DIRECTIVE if result.startswith("error:") else result
					turn_history.append({"role": "tool", "tool_call_id": call.id, "content": model_facing_result})
					tool_call_records.append({"name": call.name, "arguments": call.arguments, "result": result})
				continue

			logger.info(
				f"generate_stream_with_metadata: turn done, provider={provider_label} rounds={round_number} "
				f"total_input_tokens={sum(input_tokens_by_round)} cache_read_tokens={sum(cache_read_tokens_by_round)} "
				f"cache_creation_tokens={sum(cache_creation_tokens_by_round)}"
			)
			async for chunk in self._stream_final_answer(
				self._as_async_iter(round_chunks), schema, tapped_on_metadata, provider_label,
			):
				yield chunk
			return

		logger.info(
			f"generate_stream_with_metadata: provider={provider_label} fields={list(schema.keys())} "
			f"exceeded {MAX_TOOL_ROUNDS} tool-call rounds, forcing a final answer with tools disabled"
		)
		response_stream = self._deadline.streaming(self._active_provider.generate_stream_with_schema(
			system_prompt, turn_history, schema=schema, on_metadata=tapped_on_metadata,
		), provider_label) # type: ignore
		async for chunk in self._stream_final_answer(response_stream, schema, tapped_on_metadata, provider_label):
			yield chunk

	@staticmethod
	async def _as_async_iter(items: list[str]) -> AsyncIterator[str]:
		for item in items:
			yield item

	async def _within_deadline(self, stream: AsyncIterator[str]) -> AsyncIterator[str]:
		try:
			async for chunk in stream:
				yield chunk
		except StreamStalled:
			self._active_provider.advance()
			raise

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
			async for chunk in self._within_deadline(response_stream):
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
			logger.critical(f"{exc} -- discarding unterminated trailing field")
			if "text" not in schema:
				return
			raise

		logger.info(f"generate_stream_with_metadata: stream ended normally, provider={provider_label} accumulated_json_length={len(accumulated_json)}")
		final_parsed = partial_json_parser.parse_json(accumulated_json)
		if not isinstance(final_parsed, dict) or not final_parsed:
			return
		last_inserted = next(reversed(final_parsed))
		if last_inserted != 'text' and last_inserted not in emitted:
			on_metadata(last_inserted, final_parsed[last_inserted])
