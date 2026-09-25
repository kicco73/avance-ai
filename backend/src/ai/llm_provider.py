from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import partial_json_parser

from ai.response_schema import Field
from content_text import content_to_text, is_text_fragments  # noqa: F401 — re-exported, ai/__init__.py's own public contract
from tool_spec import ToolSpec  # noqa: F401 — re-exported, ai/__init__.py's own public contract
from system.cascade import ProviderError, ProviderRateLimitedError, ProviderUnavailableError
from system.logging_factory import LoggerFactory
from system.try_again_error import TryAgainError

logger = LoggerFactory.get_logger(__name__)
MetadataCallback = Callable[[str, Any], None]
OPENING_USER_TURN = {"role": "user", "content": "It's your turn to speak."}


def with_opening_turn(history: list[dict]) -> list[dict]:
	return history + [dict(OPENING_USER_TURN)] * (not history or history[-1]["role"] == "assistant")


@dataclass(frozen=True)
class SystemPrompt:
	"""A system prompt split into a stable prefix (identical across
	consecutive turns in the same automaton state — every channel's own
	preamble/content, the schema field order) and a volatile tail (the
	model's own memory, the env block — anything that changes turn to
	turn even while the state doesn't). Anthropic caches only the stable
	half (see AnthropicProvider._build_system); Gemini/OpenAI simply
	concatenate the two, relying on their own implicit prefix caching to
	still hit on `stable`. A plain `str` (every caller that never splits
	anything) is equivalent to SystemPrompt(stable=that string) — see
	coerce()."""
	stable: str
	volatile: str = ""

	@staticmethod
	def coerce(system_prompt: "str | SystemPrompt") -> "SystemPrompt":
		if isinstance(system_prompt, SystemPrompt):
			return system_prompt
		return SystemPrompt(stable=system_prompt)

	def full_text(self) -> str:
		"""The complete prompt text a provider with no notion of its own
		of a stable/volatile split would send — used for token-estimate
		callers (see AiService._enforce_input_budget) and by a provider
		that just concatenates the two (Gemini/OpenAI). The "\\n\\n"
		separator lives here, not inside `volatile` itself — the one
		producer (TurnProtocolUsingSchema) hands over a bare block, and
		every consumer (this method, AnthropicProvider._build_system)
		decides for itself whether/where a separator belongs; omitted
		outright when either half is empty, so a caller with only one
		half never sees a stray leading/trailing blank line."""
		if not self.stable or not self.volatile:
			return f"{self.stable}{self.volatile}"
		return f"{self.stable}\n\n{self.volatile}"


@dataclass(frozen=True)
class AIServiceConfig:
	driver: str
	model: str
	key: str
	url: str | None
	ui_label: str
	ui_description: str | None = None
	max_output_tokens: int = 1024
	token_budget_per_day: int = 1_000_000
	modes: tuple[str, ...] = ("live", "test")


class AIServiceError(ProviderError):
	"""Readable error to show on the frontend, without crashing the server."""
	message = "AI service error."


class AIServiceProviderUnavailableError(TryAgainError, ProviderUnavailableError, AIServiceError):
	"""Transient upstream overload (HTTP 503) — worth retrying."""
	message = "AI service unavailable after every retry."


class AIServiceProviderRateLimitedError(TryAgainError, ProviderRateLimitedError, AIServiceError):
	"""The upstream model API rejected the request for rate limiting (HTTP 429)."""
	message = "The AI service rate limit was exceeded."


class AIServiceProviderPermanentError(AIServiceError):
	"""Permanent provider-level failure (wrong model, invalid credentials,
	exhausted credit/quota) — never retried in place, cascades immediately."""
	message = "The AI service rejected the request."


class AIServiceRequestError(AIServiceError):
	message = "The AI service rejected the request as malformed."


class AIServiceProviderMalformedReplyError(TryAgainError, AIServiceError):
	message = "The AI service returned a reply without its text."


@dataclass(frozen=True)
class ToolCall:
	"""One invocation the model asked for, already translated out of
	whichever provider reported it — `id` is that provider's own call id
	when it has one, else generated (see each provider's own
	ToolCallsRequested-raising code)."""
	id: str
	name: str
	arguments: dict


class Thought:
	pass


class ToolCallsRequested(Exception):
	"""Raised by stream_json in place of completing the
	stream: the model ended its turn asking for one or more tools instead
	of (or before) producing a final answer. Never a failover condition
	(retrying another provider wouldn't change what the model asked for),
	so — like AIServiceProviderOutputTruncatedError — this deliberately
	doesn't subclass AIServiceError/ProviderError; it's meant to be caught
	once, by AiService's own tool-call loop, which resolves every call in
	`calls` and re-invokes the provider with the results appended to the
	(turn-local, never persisted) history."""
	def __init__(self, calls: list[ToolCall], assistant_content: Any) -> None:
		super().__init__(f"tool calls requested: {[c.name for c in calls]}")
		self.calls = calls
		self.assistant_content = assistant_content


class AIServiceProviderOutputTruncatedError(Exception):
	"""Raised by a provider's stream_json when its own
	native stop/finish reason confirms the response was cut short by
	max_output_tokens, rather than completing normally. Never a failover
	condition (retrying another provider won't raise the same cap), so it
	deliberately does not subclass AIServiceError/ProviderError — it is
	meant to be caught once, by LLMProvider.generate_stream_with_schema,
	which uses it to discard the unterminated trailing field instead of
	guessing completeness from partial JSON."""
	def __init__(self, reason: str) -> None:
		super().__init__(f"provider output truncated ({reason})")
		self.reason = reason




class TokenCounter:
	"""Thread-safe running total of tokens consumed by a provider's calls.
	Providers are shared, process-wide instances that JobQueue's worker
	threads (see jobs/job_queue.py) can call concurrently, so the counter
	itself is lock-guarded rather than a plain int."""

	def __init__(self) -> None:
		self._token_lock = threading.Lock()
		self._total_tokens = 0

	def get_total_tokens(self) -> int:
		with self._token_lock:
			return self._total_tokens

	def _add_tokens(self, count: int) -> None:
		with self._token_lock:
			self._total_tokens += count


def _ignore_metadata(name: str, value: Any) -> None:
	return None


def forward_kwargs(
	on_metadata: MetadataCallback | None, tools: list[ToolSpec] | None,
	tool_round: int = 1, required_tools: list[ToolSpec] | None = None,
) -> dict[str, Any]:
	kwargs: dict[str, Any] = {"on_metadata": on_metadata}
	if tools is not None:
		kwargs["tools"] = tools
		kwargs["tool_round"] = tool_round
		if required_tools is not None:
			kwargs["required_tools"] = required_tools
	return kwargs


class StructuredReply:
	def __init__(self, schema: dict[str, Field], on_metadata: MetadataCallback) -> None:
		self._schema = schema
		self._on_metadata = on_metadata
		self._raw = ""
		self._emitted: set[str] = {"text"}
		self._text_length = 0

	def feed(self, chunk: str) -> str:
		self._raw += chunk
		parsed = self._parsed()
		if not isinstance(parsed, dict) or not parsed:
			return ""
		still_streaming = next(reversed(parsed))
		for name in [name for name in parsed if name != still_streaming]:
			self._emit(name, parsed[name])
		text = str(parsed.get("text", ""))
		delta = text[self._text_length:]
		self._text_length = max(self._text_length, len(text))
		return delta

	def finish(self) -> None:
		parsed = self._parsed()
		if isinstance(parsed, dict) and parsed:
			last = next(reversed(parsed))
			self._emit(last, parsed[last])

	def has_text(self) -> bool:
		try:
			parsed = json.loads(self._raw)
		except json.JSONDecodeError:
			return False
		return isinstance(parsed, dict) and "text" in parsed

	def raw(self) -> str:
		return self._raw

	def _parsed(self) -> Any:
		if not self._raw.strip():
			return None
		try:
			return partial_json_parser.parse_json(self._raw)
		except ValueError as exc:
			logger.error(f"reply is not a JSON object: {exc} -- raw: {self._raw!r}")
			raise AIServiceProviderMalformedReplyError(f"the reply is not a JSON object: {exc}") from exc

	def _emit(self, name: str, value: Any) -> None:
		if name in self._emitted:
			return
		self._emitted.add(name)
		self._on_metadata(name, self._schema.get(name, Field()).coerce(value))


class LLMProvider(TokenCounter, ABC):

	def __init__(self) -> None:
		TokenCounter.__init__(self)

	async def generate_stream_with_schema(
		self, system_prompt: "str | SystemPrompt", history: list[dict], schema: dict[str, Field],
		on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None, tool_round: int = 1, required_tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str | Thought]:
		reply = StructuredReply(schema, on_metadata or _ignore_metadata)
		try:
			async for chunk in self.stream_json(
				system_prompt, history, schema, **forward_kwargs(on_metadata, tools, tool_round, required_tools),
			):
				yield chunk if isinstance(chunk, Thought) else reply.feed(chunk)
		except AIServiceProviderOutputTruncatedError as exc:
			logger.critical(f"{exc} -- discarding unterminated trailing field")
			if "text" in schema:
				raise
			return
		if "text" in schema and not reply.has_text():
			logger.error(f"reply ended without a complete JSON object carrying text -- raw: {reply.raw()!r}")
			raise AIServiceProviderMalformedReplyError("the reply ended without a complete JSON object carrying text")
		reply.finish()

	@abstractmethod
	async def stream_json(
		self, system_prompt: "str | SystemPrompt", history: list[dict], schema: dict[str, Field], on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None, tool_round: int = 1, required_tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str | Thought]:
		"""`system_prompt`: a plain str, or a SystemPrompt(stable, volatile)
		— see its own docstring. Anthropic caches `stable` alone (a plain
		str behaves as SystemPrompt(stable=str, volatile="")); Gemini/
		OpenAI just concatenate the two via SystemPrompt.full_text().

		`history` is provider-neutral, including for tool turns: beyond
		the plain {role, content} shape, two more message shapes appear —
		{"role": "assistant", "tool_calls": list[ToolCall], "content": Any}
		(the assistant's own turn asking for tools, exactly as replayed
		from a prior ToolCallsRequested) and {"role": "tool",
		"tool_call_id": str, "content": str} (one per resolved call,
		AiService's own tool loop appends these in call order). With
		`tools` empty/None, a provider must send the exact same request it
		always has — no tool-call machinery engaged at all. When the model
		ends its turn asking for tools instead of completing normally,
		raise ToolCallsRequested in place of finishing the stream.

		`required_tools`: non-empty only for the one round AiService itself
		decided must force a call (see its own generate_stream_with_metadata
		and TrackingProcessor.force_required_tools_for) — a subset of
		`tools` the model must be restricted to calling from, in that
		provider's own tool_choice/function_calling_config dialect, rather
		than left free to also just answer. None/empty means this round is
		`auto`, exactly as tool-calling has always behaved. `tool_round` is
		AiService's own 1-based round counter, informational only (e.g. for
		a provider's own log line) — the forcing decision itself is always
		`required_tools`'s own presence, never this number."""
		raise NotImplementedError
		yield

	def advance(self) -> None:
		return None

	@abstractmethod
	def get_input_tokens(self, prompt: str) -> int:
		"""Estimated token count `prompt` would cost as input, computed
		each provider's own way (a real count-tokens API call, or a local
		tokenizer estimate) — never a network call to actually generate."""
		raise NotImplementedError

