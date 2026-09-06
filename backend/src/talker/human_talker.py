"""HumanTalker — the BaseTalker whose replies come from a person instead
of the model. Same three methods, same signatures as AiTalker, so a
caller that already holds a BaseTalker (TrackingProcessor, ChatController,
WhatsAppService) doesn't change at all when the session's talker switches;
only which concrete class got constructed changes.

It knows nothing about *how* a particular person is reached — WebSocket,
WhatsApp, whatever comes next — that's HumanRelay's job, injected at
construction. HumanTalker only knows the shape of the three calls: hand
the relay something to show the person, wait for what they send back, and
(for audio) ask the relay for their own original recording rather than
ever synthesizing one.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator, Protocol

from ai import content_to_text
from listen.listen_service import ListenService, ListenServiceNotAvailableError

from .base_talker import BaseTalker

if TYPE_CHECKING:
	from ai import MetadataCallback
	from tracking.channels import MetadataChannel
	from tracking.sources import ToolSet


class HumanRelay(Protocol):
	"""Whatever gets a message in front of one specific person and back:
	implemented once per transport (WebSocket notification, WhatsApp
	message, ...) — HumanTalker never talks to a transport directly."""

	async def notify(self, prompt_text: str) -> None:
		"""Surfaces `prompt_text` — the conversation's latest turn — to
		the person, however this relay reaches them."""
		...

	async def receive(self) -> str:
		"""Waits for and returns what the person sends back, as text
		(a voice note is transcribed by the caller through listen(),
		same as any other inbound audio — receive() only ever returns
		the text)."""
		...

	async def wait_for_typing(self) -> None:
		"""Resolves the moment the person starts composing a reply (their
		own frontend's own signal — see chat/ws_notifications.py's
		human_typing handling) — races against receive() in chat() below,
		so a typing indicator only ever shows once someone is actually
		there, never automatically from the instant they're asked."""
		...

	async def recorded_audio(self, text: str) -> AsyncIterator[bytes] | None:
		"""The person's own original recording for a reply whose text is
		`text`, if one exists — None when they typed instead of speaking,
		or the recording is gone. Never a synthesized fallback: that
		choice belongs to the caller (see HumanTalkerNoRecordingError)."""
		...


class HumanTalkerNoRecordingError(Exception):
	"""Raised by talk() when the relay has no original recording for the
	requested text — HumanTalker never falls back to synthesizing one."""

	def __init__(self, message: str = "No original recording is available for this reply.") -> None:
		super().__init__(message)


class HumanTalker(BaseTalker):
	def __init__(self, relay: HumanRelay, listen_service: ListenService | None = None) -> None:
		self._relay = relay
		self._listen_service = listen_service

	async def chat(
		self,
		channels: list["MetadataChannel"],
		chat_history: list[dict],
		on_metadata: "MetadataCallback",
		tool_set: "ToolSet | None" = None,
		force_required_tools: bool = False,
		env_block: str | None = None,
	) -> AsyncIterator[str]:
		"""Same signature as AiTalker.chat(), read differently: `channels`/
		`tool_set`/`force_required_tools`/`env_block` are the model's own
		prompt-construction concerns and go unused here — the person sees
		the conversation itself, not the schema built to ask a model about
		it. `chat_history`'s last entry is the turn the person is replying
		to; on_metadata is never called for signals/reaction/translations/
		tool_call, the fields only the model's own auto-tracking pass
		produces (see AiTalker used a second time, as that filter — a
		separate call this method doesn't make).

		Not a real stream: an empty chunk only once the person actually
		starts typing (never automatically the instant they're notified —
		that would show a typing indicator for however long they take to
		even open the page), then the whole reply once they send it. The
		empty chunk is skipped entirely if the reply itself wins the race
		(see wait_for_typing's own docstring) — a reply typed and sent
		fast enough that no separate typing signal ever arrived first."""
		prompt_text = content_to_text(chat_history[-1]["content"]) if chat_history else ""
		await self._relay.notify(prompt_text)
		typing = asyncio.ensure_future(self._relay.wait_for_typing())
		reply = asyncio.ensure_future(self._relay.receive())
		done, _ = await asyncio.wait({typing, reply}, return_when=asyncio.FIRST_COMPLETED)
		if reply in done:
			typing.cancel()
			yield reply.result()
			return
		yield ""
		yield await reply

	async def listen(self, audio: bytes) -> str:
		"""Speech-to-text for a voice note the person sent — same STT as
		AiTalker.listen(): transcription is a mechanical service, not a
		property of who's speaking."""
		if self._listen_service is None:
			raise ListenServiceNotAvailableError()
		return await self._listen_service.transcribe(audio)

	def talk(self, text: str) -> AsyncIterator[bytes]:
		"""Audio for a reply the person actually spoke — their own
		recording, never synthesized. Raises HumanTalkerNoRecordingError
		when the relay has none (they typed this reply)."""
		return self._replay_recording(text)

	async def _replay_recording(self, text: str) -> AsyncIterator[bytes]:
		recording = await self._relay.recorded_audio(text)
		if recording is None:
			raise HumanTalkerNoRecordingError()
		async for chunk in recording:
			yield chunk
