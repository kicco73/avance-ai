import asyncio
from dataclasses import dataclass
import logging

from automaton.automaton import Action, Automaton, State
from chat.tracking_processor import OutVariables, TrackingProcessor


logger = logging.getLogger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):

	@dataclass
	class Parameters:
		signal_row_id: State

	def on_receiving_metadata_that_may_trigger_status_change(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._FIXME_would_trigger_action()
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
			print("AUDIO ***************************", rv)
		self.metadata.on_metadata(key, rv)

	def on_receiving_metadata_when_repeating_the_call(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
		elif key == 'env':
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

			self.out.tracking_id = self._FIXME_move_automaton()

			async for chunk in self.generate_reply(self.out.state, self.on_receiving_metadata_when_repeating_the_call):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		return self.out
