"""AiTalker — the one object each call site talks to instead of wiring
straight to AiService/TalkService/ListenService. It doesn't add any new
behaviour: chat()/listen()/talk() are exactly the calls that used to be
made directly (TurnProtocolUsingSchema(ai_service).generate_reply(...),
talk_service.generate(...), listen_service.transcribe(...)), just behind
one name — so a caller-side substitution (a different chat()/listen()/
talk() implementation, e.g. a human answering instead of the model) has
a single seam instead of three scattered ones.

Each of the three services is optional: a caller that only ever needs
listen()/talk() (ChatController, WhatsAppService) builds an AiTalker
without an ai_service, and one that only ever needs chat() (TrackingProcessor)
builds one without talk_service/listen_service. Calling a method whose
service wasn't supplied raises the same *NotAvailableError the direct call
would have raised.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from listen.listen_service import ListenService, ListenServiceNotAvailableError
from talk.talk_service import TalkService, TalkServiceNotAvailableError
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

if TYPE_CHECKING:
	from ai import MetadataCallback
	from ai.ai_service import AiService
	from tracking.channels import MetadataChannel
	from tracking.sources import ToolSet


class AiTalker:
	def __init__(
		self,
		ai_service: "AiService | None" = None,
		talk_service: TalkService | None = None,
		listen_service: ListenService | None = None,
	) -> None:
		self._ai_service = ai_service
		self._talk_service = talk_service
		self._listen_service = listen_service

	def chat(
		self,
		channels: list["MetadataChannel"],
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
			channels, chat_history, on_metadata,
			tool_set=tool_set, force_required_tools=force_required_tools, env_block=env_block,
		)

	async def listen(self, audio: bytes) -> str:
		"""Speech-to-text for one audio message — same call as
		listen_service.transcribe(audio) always made. Raises
		ListenServiceNotAvailableError if no ListenService was supplied,
		same as a caller checking `listen_service is None` itself used to."""
		if self._listen_service is None:
			raise ListenServiceNotAvailableError()
		return await self._listen_service.transcribe(audio)

	def talk(self, text: str) -> AsyncIterator[bytes]:
		"""Text-to-speech for one reply — same call as talk_service.
		generate(text) always made. Raises TalkServiceNotAvailableError if
		no TalkService was supplied, same as a caller checking
		`talk_service is None` itself used to."""
		if self._talk_service is None:
			raise TalkServiceNotAvailableError()
		return self._talk_service.generate(text)
