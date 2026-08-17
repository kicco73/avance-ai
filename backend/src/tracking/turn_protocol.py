from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ai.ai_service import AiService
from ai.llm_provider import MetadataCallback
from tracking.env import Env

class TurnProtocol(ABC):

	prompt_preambles: dict[str,str] = {}

	def __init__(self, ai_service: AiService, evaluate_signals_first) -> None:
		self._ai_service = ai_service
		if evaluate_signals_first:
			self.include_tags = 'signals', 'audio', 'text', 'env'
		else:
			self.include_tags = 'audio', 'text', 'signals', 'env'

	def generate_reply(
		self,
		base_prompt: str,
		signal_definition: str | None,
		env: Env,
		chat_history: list[dict],
		on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		"""Returns chunks of text coming from the response streaming and
		calls metadata callback to handle tags in a compatible way for V1 and V2.
		"""
		final_prompt = self.__build_prompt(text=base_prompt, env=env.serialise_as_text(), signals=signal_definition, audio=None)
		return self._generate_reply(final_prompt, chat_history, on_metadata)

	@abstractmethod
	def _generate_reply(self, prompt: str, chat_history: list[dict], on_metadata: MetadataCallback,) -> AsyncIterator[str]:
		raise NotImplementedError

	@abstractmethod
	def generate_reply_with_schema(
		self, base_prompt: str, tag_specs: list[tuple[str, str]], chat_history: list[dict], on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		raise NotImplementedError

	def __build_prompt(self, **kw) -> str:

		content = []
		for tag in self.include_tags:
			content += [self.prompt_preambles[tag], kw.get(tag) or ""]

		return "\n\n".join(content)
