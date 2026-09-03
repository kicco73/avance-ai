from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ai import AiService
from ai import MetadataCallback
from tracking.env import Env

class TurnProtocol(ABC):

	prompt_preambles: dict[str,str] = {}

	def __init__(
		self, ai_service: AiService, evaluate_signals_first, reactions_enabled: bool = False, talk_enabled: bool = True,
	) -> None:
		self._ai_service = ai_service
		# 'reaction' is the one conditional tag here — every other one is
		# always present. Its presence, not a separate check afterward, is
		# what enforces State.reactions_enabled (see automaton.State).
		reaction_tags = ('reaction',) if reactions_enabled else ()
		audio_tags = ('audio',) if talk_enabled else ()
		if evaluate_signals_first:
			self.include_tags = ('signals', *reaction_tags, *audio_tags, 'text', 'env')
		else:
			self.include_tags = (*audio_tags, 'text', 'signals', *reaction_tags, 'env')

	def generate_reply(
		self,
		base_prompt: str,
		signal_definition: str | None,
		env: Env,
		chat_history: list[dict],
		on_metadata: MetadataCallback,
		# Same role as signal_definition — the project's own reaction
		# vocabulary (name + definition), so the model actually knows what
		# a 'reaction' value could be, not just that the field exists.
		# Default None: every caller that never enables reactions (the
		# common case for most existing tests) is unaffected.
		reaction_definition: str | None = None,
	) -> AsyncIterator[str]:
		"""Returns chunks of text coming from the response streaming and
		calls metadata callback to handle tags in a compatible way for V1 and V2.
		"""
		final_prompt = self.build_final_prompt(base_prompt, signal_definition, env, reaction_definition)
		return self._generate_reply(final_prompt, chat_history, on_metadata)

	def build_final_prompt(
		self, base_prompt: str, signal_definition: str | None, env: Env, reaction_definition: str | None = None,
	) -> str:
		"""The exact system_prompt generate_reply() sends to the AI — split
		out so a caller that only wants the rendered text (e.g. a token
		estimate) doesn't have to trigger a real generation call to get it."""
		return self.__build_prompt(
			text=base_prompt, env=env.serialise_as_text(), signals=signal_definition,
			reaction=reaction_definition, audio=None,
		)

	@abstractmethod
	def _generate_reply(self, prompt: str, chat_history: list[dict], on_metadata: MetadataCallback,) -> AsyncIterator[str]:
		raise NotImplementedError

	@abstractmethod
	def generate_reply_with_schema(
		self, base_prompt: str, env: Env, tag_specs: list[tuple[str, str]], chat_history: list[dict],
		on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		raise NotImplementedError

	def __build_prompt(self, **kw) -> str:

		content = []
		for tag in self.include_tags:
			content += [self.prompt_preambles[tag], kw.get(tag) or ""]

		return "\n\n".join(content)
