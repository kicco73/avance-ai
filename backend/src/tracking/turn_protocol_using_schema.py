from __future__ import annotations

from typing import AsyncIterator

from ai import MetadataCallback, SystemPrompt
from logging_factory import LoggerFactory
from tracking.channels import MemoryChannel, MetadataChannel
from tracking.sources import ToolSet

logger = LoggerFactory.get_logger(__name__)

SCHEMA_ORDER_PROMPT = """"
Respond with the structured JSON object described by the response
schema, filling in its fields in this order:
"""


def _tool_set_kwargs(tool_set: ToolSet | None, force_required_tools: bool = False) -> dict:
	"""`tool_set` only actually forwarded when given — a fake/stub
	AiService predating tool-calling (most existing tests' own doubles)
	declares no `tool_set` parameter at all, so it must keep receiving
	the exact same call it always did, never a stray `tool_set=None` it
	can't accept.

	`force_required_tools` is included only when actually True, same
	reasoning: a fake/stub AiService predating ai-must-read-sources
	(every existing tool-calling test's own double) declares no such
	parameter either, and none of them exercises a state with anything to force."""
	if tool_set is None:
		return {}
	kwargs: dict = {"tool_set": tool_set}
	if force_required_tools:
		kwargs["force_required_tools"] = force_required_tools
	return kwargs


class TurnProtocolUsingSchema:
	"""Drives one turn's own AI generation call against a caller-supplied,
	already-ordered list of MetadataChannel — building the prompt and JSON
	schema from them, and decoding each field's raw response through its
	own channel before handing it to `on_metadata`. The channel list is
	the entire configuration surface: which fields are asked for, in what
	order, with what per-turn content — nothing here has any opinion of
	its own about that."""

	def __init__(self, ai_service) -> None:
		self._ai_service = ai_service

	def build_final_prompt(self, channels: list[MetadataChannel]) -> str:
		"""The exact system_prompt generate_reply() would send for
		`channels`, minus the trailing SCHEMA_ORDER_PROMPT field-order
		instructions — split out so a caller that only wants the
		rendered text (e.g. a token estimate) doesn't have to trigger a
		real generation call to get it."""
		parts = []
		for channel in channels:
			parts += [channel.preamble, channel.content]
		return "\n\n".join(parts)

	@staticmethod
	def schema_overhead_text(channels: list[MetadataChannel]) -> str:
		"""Every fixed bit of text generate_reply adds on top of each
		channel's own dynamic `content` — every channel's own preamble
		plus SCHEMA_ORDER_PROMPT's field-order instructions. Exposed
		separately, read-only, so TrackingProcessor._enforce_input_budget
		can size it without a real generation call."""
		preambles = "".join(channel.preamble for channel in channels)
		order = "\n".join(f'\t- {channel.tag}' for channel in channels)
		return f"{preambles}{SCHEMA_ORDER_PROMPT}\n{order}"

	def generate_reply(
		self, channels: list[MetadataChannel], chat_history: list[dict], on_metadata: MetadataCallback,
		tool_set: ToolSet | None = None, force_required_tools: bool = False, env_block: str | None = None,
	) -> AsyncIterator[str]:
		"""Returns chunks of text coming from the response streaming,
		calling on_metadata for each non-"text" field as it completes —
		with its raw value already decoded through the matching channel
		(see `channels`), or passed through unchanged for a key with no
		matching channel (input_tokens/output_tokens/tool_call/tool_result
		— internal AiService plumbing, never a real schema field).

		`env_block` (see tracking.env_prompt_block.EnvPromptBlock): the
		automaton's own declared variables, read-only context for the
		model — never a response field of its own, so it's appended as
		plain text after every channel's own preamble/content and the
		schema's field-order instructions, the very last thing in the
		system prompt. Kept last deliberately: it's the one piece of this
		prompt that can change turn to turn independently of which
		channels are active, so keeping it last maximizes how much of the
		prompt stays a stable, cacheable prefix."""
		order = "\n".join(f'\t- {channel.tag}' for channel in channels)
		prompt = f"{self.build_final_prompt(channels)}\n\n{SCHEMA_ORDER_PROMPT}\n{order}"
		if env_block:
			prompt = f"{prompt}\n\n{env_block}"
		schema = {channel.tag: channel.schema_description for channel in channels}
		channel_by_tag = {channel.tag: channel for channel in channels}

		def decoding_on_metadata(tag: str, raw) -> None:
			channel = channel_by_tag.get(tag)
			on_metadata(tag, channel.decode(raw) if channel is not None else raw)

		return self._ai_service.generate_stream_with_metadata(
			prompt, chat_history, on_metadata=decoding_on_metadata, schema=schema,
			**_tool_set_kwargs(tool_set, force_required_tools),
		)
