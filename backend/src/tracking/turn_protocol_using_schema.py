from __future__ import annotations

from typing import AsyncIterator

from ai import MetadataCallback, SystemPrompt
from logging_factory import LoggerFactory
from tracking.prompt import Prompt
from tracking.sources import ToolSet

logger = LoggerFactory.get_logger(__name__)


def _tool_set_kwargs(tool_set: ToolSet | None, force_required_tools: bool = False) -> dict:
	"""`tool_set` only actually forwarded when given — a fake/stub
	AiService predating tool-calling (most existing tests' own doubles)
	declares no `tool_set` parameter at all, so it must keep receiving
	the exact same call it always did, never a stray `tool_set=None` it
	can't accept.

	`force_required_tools` is included only when actually given, same
	reasoning: a fake/stub AiService predating it declares no such
	parameter, and none of them exercises a state with anything to
	force."""
	if tool_set is None:
		return {}
	kwargs: dict = {"tool_set": tool_set}
	if force_required_tools:
		kwargs["force_required_tools"] = force_required_tools
	return kwargs


class TurnProtocolUsingSchema:
	"""Drives one turn's own AI generation call against a caller-supplied,
	already-composed Prompt — handing its rendered SystemPrompt and JSON
	schema to AiService, and decoding each field's raw response through
	the matching channel before passing it to `on_metadata`. `prompt` is
	the entire configuration surface: which channels are asked for, in
	what order, with what per-turn content — nothing here has any opinion
	of its own about that (see Prompt.chain, the composition/ordering
	primitive callers build `prompt` with)."""

	def __init__(self, ai_service) -> None:
		self._ai_service = ai_service

	def generate_reply(
		self, prompt: Prompt, chat_history: list[dict], on_metadata: MetadataCallback,
		tool_set: ToolSet | None = None, force_required_tools: bool = False, env_block: str | None = None,
	) -> AsyncIterator[str]:
		"""Returns chunks of text coming from the response streaming,
		calling on_metadata for each non-"text" field as it completes —
		with its raw value already decoded through the matching channel
		(see `prompt.decode_channel`), or passed through unchanged for a
		key with no matching channel (input_tokens/output_tokens/tool —
		internal AiService plumbing, never a real schema field).

		The system prompt handed to AiService is `prompt`'s own
		SystemPrompt (see Prompt.to_system_prompt), split so a provider
		that caches a prefix (see AnthropicProvider._build_system) can
		actually hit that cache across consecutive turns in the same
		automaton state. `env_block` (the automaton's own declared
		variables, read-only context for the model, never a response
		field of its own — see tracking.env_prompt_block.EnvPromptBlock)
		is appended to the volatile half here, on top of whatever
		`prompt` already put there (MemoryPrompt's own "Current memory:"
		header and content)."""
		system_prompt = prompt.to_system_prompt()
		if env_block:
			volatile = f"{system_prompt.volatile}\n\n{env_block}" if system_prompt.volatile else env_block
			system_prompt = SystemPrompt(stable=system_prompt.stable, volatile=volatile)

		def decoding_on_metadata(tag: str, raw) -> None:
			on_metadata(tag, prompt.decode_channel(tag, raw))

		return self._ai_service.generate_stream_with_metadata(
			system_prompt, chat_history, on_metadata=decoding_on_metadata, schema=prompt.schema(),
			**_tool_set_kwargs(tool_set, force_required_tools),
		)
