"""AiTalker — the one object each call site talks to instead of wiring
straight to AiService/TalkService. It doesn't add any new behaviour:
chat()/talk() are exactly the calls that used to be made directly
(TurnProtocolUsingSchema(ai_service).generate_reply(...),
talk_service.generate(...)), just behind one name — so a caller-side
substitution (a different chat()/talk() implementation, e.g. a human
answering instead of the model) has a single seam instead of two
scattered ones.

Both services are optional: a caller that only ever needs talk()
(ChatController, WhatsAppService) builds an AiTalker without an
ai_service, and one that only ever needs chat() (TrackingProcessor)
builds one without a talk_service. Calling a method whose service wasn't
supplied raises the same *NotAvailableError the direct call would have
raised.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from talk.talk_service import TalkService, TalkServiceNotAvailableError
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

from .base_talker import BaseTalker

if TYPE_CHECKING:
	from ai import AiService, MetadataCallback
	from tracking.prompt import Prompt
	from tracking.sources import ToolSet


class AiTalker(BaseTalker):
	def __init__(
		self,
		ai_service: "AiService | None" = None,
		talk_service: TalkService | None = None,
	) -> None:
		self._ai_service = ai_service
		self._talk_service = talk_service

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
		no TalkService was supplied, same as a caller checking
		`talk_service is None` itself used to."""
		if self._talk_service is None:
			raise TalkServiceNotAvailableError()
		return self._talk_service.generate(text)
