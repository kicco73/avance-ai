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
		self, system_prompt: str, history: list[dict], schema: dict[str, str]
	) -> AsyncIterator[str]:
		raise NotImplementedError
		yield

	@abstractmethod
	def get_input_tokens(self, prompt: str) -> int:
		"""Estimated token count `prompt` would cost as input, computed
		each provider's own way (a real count-tokens API call, or a local
		tokenizer estimate) — never a network call to actually generate."""
		raise NotImplementedError

