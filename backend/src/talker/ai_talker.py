"""AiTalker — the one object each call site talks to instead of wiring
straight to AiService/TalkService. It doesn't add any new behaviour:
chat()/talk() are exactly the calls that used to be made directly
(TurnProtocolUsingSchema(ai_service).generate_reply(...),
talk_service.generate(...)), just behind one name — so a caller-side
substitution (a different chat()/talk() implementation, e.g. a human
answering instead of the model) has a single seam instead of two
scattered ones.

ai_service is optional: a caller that only ever needs talk()
(WhatsAppService) builds an AiTalker without one. talk() asks the Bus
rather than any object it was handed: a build with nothing registered
for `output.speech` produces no audio and the caller falls back to
text, which is the same answer it always had for an unconfigured
talk-service.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator

from system import bus
from system.bus import OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, Message
from system.session import Session
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

	async def talk(self, text: str) -> AsyncIterator[bytes]:
		"""Text-to-speech for one reply: `output.speech` goes out, and
		whatever `output.audio_stream` comes back on this exchange's own
		origin is the answer, drained here chunk by chunk as it is
		generated. Yields nothing when nobody is registered to speak —
		the same "no audio, send the text" the caller already handles."""
		origin = f"speech:{uuid.uuid4()}"
		streams: dict[str, Any] = {}

		async def take(message: Message) -> None:
			streams[str(message.origin_id)] = message.body

		bus.subscribe(OUTPUT_AUDIO_STREAM, take)
		try:
			await bus.publish(Message(
				type=OUTPUT_SPEECH, body=text, username=Session().user, origin_id=origin,
			))
		finally:
			bus.unsubscribe(OUTPUT_AUDIO_STREAM, take)
		for stream in filter(None, [streams.get(origin)]):
			async for chunk in stream.chunks():
				yield chunk
