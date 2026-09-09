"""AiTalker — the one object each call site talks to instead of wiring
straight to AiService/TalkService. It doesn't add any new behaviour:
chat()/talk() are exactly the calls that used to be made directly
(TurnProtocolUsingSchema(ai_service).generate_reply(...),
talk_service.generate(...)), just behind one name — so a caller-side
substitution (a different chat()/talk() implementation, e.g. a human
answering instead of the model) has a single seam instead of two
scattered ones.

ai_service is optional: a caller that only ever needs talk()
(PlatformController, WhatsAppService) builds an AiTalker without one.
talk() itself is always available or not depending on whether a talk
skill is installed/configured, checked fresh on every call rather than
supplied at construction; calling chat() without an ai_service raises
the same *NotAvailableError the direct call would have raised.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from system import bus
from system.bus import POINT_TALK_PROVIDER
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

from .base_talker import BaseTalker, TalkServiceNotAvailableError

if TYPE_CHECKING:
	from ai import AiService, MetadataCallback
	from tracking.prompt import Prompt
	from tracking.sources import ToolSet


class AiTalker(BaseTalker):
	def __init__(
		self,
		ai_service: "AiService | None" = None,
	) -> None:
		self._ai_service = ai_service

	def chat(
		self,
		prompt: "Prompt",
		chat_history: list[dict],
		on_metadata: "MetadataCallback",
		tool_set: "ToolSet | None" = None,
		force_required_tools: bool = False,
		env_block: str | None = None,
	) -> AsyncIterator[str]:
		"""One turn's own text generation — same call, same streamed
		chunks and on_metadata callbacks as TurnProtocolUsingSchema(
		ai_service).generate_reply(...) always made, just reached through
		this object instead of built inline at the call site."""
		assert self._ai_service is not None, "AiTalker built without an ai_service can't chat()"
		return TurnProtocolUsingSchema(self._ai_service).generate_reply(
			prompt, chat_history, on_metadata,
			tool_set=tool_set, force_required_tools=force_required_tools, env_block=env_block,
		)

	def talk(self, text: str) -> AsyncIterator[bytes]:
		"""Text-to-speech for one reply — same call as talk_service.
		generate(text) always made. Raises TalkServiceNotAvailableError if
		no talk skill is installed/configured, same as a caller checking
		`talk_service is None` itself used to."""
		generate = bus.collect(POINT_TALK_PROVIDER, {}).get("generate")
		if generate is None:
			raise TalkServiceNotAvailableError()
		return generate(text)
