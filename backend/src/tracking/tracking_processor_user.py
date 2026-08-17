import asyncio
from dataclasses import dataclass
import logging

from automaton.automaton import Action, Automaton, State
from tracking.tracking_processor import OutVariables, TrackingProcessor


logger = logging.getLogger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):

	@dataclass
	class Parameters:
		signal_row_id: State

	def on_receiving_metadata_that_may_trigger_status_change(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._tracking_engine.evaluate_triggered_action(
				self.user.automaton, self.user.state, self.metadata.signals
			)
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
			print("AUDIO ***************************", rv)
		self.metadata.on_metadata(key, rv)

	def on_receiving_metadata_when_repeating_the_call(self, key: str, value: str):
		# 'signals' is never among tag_specs for this call anymore (see
		# _get_ai_reply's regeneration call) — already known from the
		# first call, re-requesting them would be wasted and must not
		# trigger a second trigger evaluation.
		rv = value
		if key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)

		# Optimistic guess: generate the real reply first, using the
		# *current* state's own context (see this module's own
		# docstring) — the common case (no transition) needed exactly
		# this one call anyway.

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_that_may_trigger_status_change):
			if self.user.state == self.out.state:
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		if self.user.state != self.out.state:
			# Wrong guess — the async method moved the automaton.
			# We need to regenerate the answer

			self.out.reply = ""
			self.metadata.on_metadata('text', "")

			# need to save state transition

			self.out.tracking_id = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id
			)

			# Signals are already known from the first call — asking
			# again here would be wasted (and must not trigger a second
			# trigger evaluation, see on_receiving_metadata_when_
			# repeating_the_call, which never handles 'signals'), so
			# this regeneration only ever requests audio/text/env.
			base_prompt, chat_history = self._build_base_prompt_and_history(self.out.state)
			async for chunk in self.build_turn_protocol().generate_reply_with_schema(
				base_prompt,
				tag_specs=[('audio', 'audio'), ('text', 'text'), ('env', 'env')],
				chat_history=chat_history,
				on_metadata=self.on_receiving_metadata_when_repeating_the_call,
			):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		return self.out
