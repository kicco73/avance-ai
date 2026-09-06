"""BaseTalker — the contract shared by whoever is answering on the
automaton's side of the conversation: the model (AiTalker) today, a
person (HumanTalker) from now on, and whatever else replaces one of
them later. A call site that only knows it's holding a BaseTalker
doesn't know or care which — chat()/listen()/talk() mean the same thing
either way: produce the reply text for this turn, transcribe an inbound
audio message, and produce the audio for an outbound one."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
	from ai import MetadataCallback
	from tracking.prompt import Prompt
	from tracking.sources import ToolSet


class BaseTalker(ABC):
	@abstractmethod
	def chat(
		self,
		prompt: "Prompt",
		chat_history: list[dict],
		on_metadata: "MetadataCallback",
		tool_set: "ToolSet | None" = None,
		force_required_tools: bool = False,
		env_block: str | None = None,
	) -> AsyncIterator[str]:
		"""This turn's own reply text, as it becomes available — chunk by
		chunk for AiTalker, in whatever pieces the implementation can
		actually offer for anyone else (see HumanTalker: one empty chunk,
		then the whole reply). `on_metadata` is called for every non-text
		field a concrete implementation is able to produce; one that can't
		produce a given field (HumanTalker and signals/reaction/
		translations/tool_call, today) simply never calls it for that tag —
		never a placeholder, never a default."""
		raise NotImplementedError

	@abstractmethod
	async def listen(self, audio: bytes) -> str:
		"""Speech-to-text for one inbound audio message."""
		raise NotImplementedError

	@abstractmethod
	def talk(self, text: str) -> AsyncIterator[bytes]:
		"""Audio bytes for one outbound reply's text — synthesized for
		AiTalker, the speaker's own original recording for HumanTalker
		when one exists."""
		raise NotImplementedError
