from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable


from cascade import ProviderError, ProviderRateLimitedError, ProviderUnavailableError
from logging_factory import LoggerFactory
from try_again_error import TryAgainError

logger = LoggerFactory.get_logger(__name__)

# Called synchronously, fire-and-forget — never awaited by a provider.
# Each metadata key ("audio", "signals", "env", ...) fires at most once
# per turn; callers needing to await something schedule their own task.
MetadataCallback = Callable[[str, Any], None]
@dataclass(frozen=True)
class AIServiceConfig:
	driver: str
	model: str
	key: str
	url: str | None
	# Optional: falls back to `driver` (see AppConfig._parse_ai_services).
	ui_label: str
	ui_description: str | None = None
	max_output_tokens: int = 1024
	# Advisory ceiling for Manage services' own consumption bar (see
	# AiService.generate_stream_with_metadata's on_metadata tap and
	# db/ai_usage.py) — display-only, like total_token_budget_per_session;
	# nothing here throttles or blocks a call that goes over it.
	token_budget_per_day: int = 1_000_000
	# Which cascade(s) this entry participates in (see AiService.for_live/
	# for_test, which each filter on this independently) — some
	# combination of "live"/"test", or empty to sit in neither. Defaults
	# to both when `modes` is absent from config.yml entirely (see
	# AppConfig._parse_ai_services); an explicit empty list is different
	# from that default — it deliberately excludes the entry from both.
	# "no-auto" is a third, separate tag applied alongside "live"/"test"
	# (never on its own) — see AiService.for_live/for_test's own
	# auto_config_indices for what it actually does.
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


@dataclass(frozen=True)
class ToolSpec:
	"""One callable a provider's own native tool-calling exposes to the
	model this turn — see tracking.sources.ToolSet.specs(), the only
	producer of these today. `name` is always "source_<source
	name>_<method>"; `parameters` is a JSON Schema object (every property
	a plain string, all required — ToolSet.specs()'s own contract)."""
	name: str
	description: str
	parameters: dict


@dataclass(frozen=True)
class ToolCall:
	"""One invocation the model asked for, already translated out of
	whichever provider reported it — `id` is that provider's own call id
	when it has one, else generated (see each provider's own
	ToolCallsRequested-raising code)."""
	id: str
	name: str
	arguments: dict


class ToolCallsRequested(Exception):
	"""Raised by generate_stream_with_schema in place of completing the
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
		# The provider-neutral assistant message to replay in history —
		# whatever text (if any) the model produced alongside asking for
		# these calls; see the neutral {"role": "assistant", "tool_calls":
		# ..., "content": ...} message shape each provider must translate
		# to/from its own format.
		self.assistant_content = assistant_content


class AIServiceProviderOutputTruncatedError(Exception):
	"""Raised by a provider's generate_stream_with_schema when its own
	native stop/finish reason confirms the response was cut short by
	max_output_tokens, rather than completing normally. Never a failover
	condition (retrying another provider won't raise the same cap), so it
	deliberately does not subclass AIServiceError/ProviderError — it is
	meant to be caught once, by AiService.generate_stream_with_metadata,
	which uses it to discard the unterminated trailing field instead of
	guessing completeness from partial JSON."""
	def __init__(self, reason: str) -> None:
		super().__init__(f"provider output truncated ({reason})")
		self.reason = reason


def content_to_text(content: Any, provider_name: str = "LLM") -> str:
	"""Flattens provider-neutral attachment blocks to plain text.
	Binary (base64) attachments are skipped if unsupported.
	"""
	if isinstance(content, str):
		return content
	parts: list[str] = []
	for block in content:
		source = block["source"]
		if source["type"] == "text":
			parts.append(f"[Attachment: {block['filename']}]\n{source['data']}")
		else:
			logger.warning(
				"Skipping unsupported binary attachment '%s' for %s.",
				block["filename"],
				provider_name,
			)
	return "\n\n".join(parts)


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


class LLMProvider(TokenCounter, ABC):

	def __init__(self) -> None:
		TokenCounter.__init__(self)

	@abstractmethod
	async def generate_stream_with_schema(
		self, system_prompt: str, history: list[dict], schema: dict[str, str], on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str]:
		"""`history` is provider-neutral, including for tool turns: beyond
		the plain {role, content} shape, two more message shapes appear —
		{"role": "assistant", "tool_calls": list[ToolCall], "content": Any}
		(the assistant's own turn asking for tools, exactly as replayed
		from a prior ToolCallsRequested) and {"role": "tool",
		"tool_call_id": str, "content": str} (one per resolved call,
		AiService's own tool loop appends these in call order). With
		`tools` empty/None, a provider must send the exact same request it
		always has — no tool-call machinery engaged at all. When the model
		ends its turn asking for tools instead of completing normally,
		raise ToolCallsRequested in place of finishing the stream."""
		raise NotImplementedError
		yield

	@abstractmethod
	def get_input_tokens(self, prompt: str) -> int:
		"""Estimated token count `prompt` would cost as input, computed
		each provider's own way (a real count-tokens API call, or a local
		tokenizer estimate) — never a network call to actually generate."""
		raise NotImplementedError

