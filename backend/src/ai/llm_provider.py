from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable


from cascade import OnRetry, ProviderError, ProviderRateLimitedError, ProviderUnavailableError

logger = logging.getLogger(__name__)

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


class AIServiceError(ProviderError):
	"""Readable error to show on the frontend, without crashing the server."""
	message = "AI service error."


class AIServiceProviderUnavailableError(ProviderUnavailableError, AIServiceError):
	"""Transient upstream overload (HTTP 503) — worth retrying."""
	message = "AI service unavailable after every retry."


class AIServiceProviderRateLimitedError(ProviderRateLimitedError, AIServiceError):
	"""The upstream model API rejected the request for rate limiting (HTTP 429)."""
	message = "The AI service rate limit was exceeded."


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


class LLMProvider(ABC):

	async def generate(
		self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None
	) -> str:
		chunks: list[str] = []
		async for chunk in self.generate_stream(system_prompt, history):
			chunks.append(chunk)
		return "".join(chunks)

	@abstractmethod
	async def generate_stream(
		self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None
	) -> AsyncIterator[str]:
		raise NotImplementedError
		yield

class LLMProviderWithSchema(ABC):

	@abstractmethod
	async def generate_stream_with_schema(
		self, system_prompt: str, history: list[dict], schema: dict[str, str]
	) -> AsyncIterator[str]:
		raise NotImplementedError
		yield

