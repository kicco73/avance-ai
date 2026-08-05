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

	async def on_signals_async(self):
		guessed_action, new_state, transition_messages, tracking_id = await self._run_auto_tracking()
		if tracking_id is not None and self.user.message_id:
			self.db.link_signal_to_message(tracking_id, self.user.message_id)

		self.out.action = guessed_action
		self.out.state = new_state
		self.out.messages = transition_messages
		self.out.tracking_id = None 

	def on_receiving_metadata_that_may_trigger_status_change(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			asyncio.create_task(self.on_signals_async())
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

		async for chunk in self.generate_reply(self.on_receiving_metadata_that_may_trigger_status_change):
			if self.user.state == self.out.state:
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		if self.user.state != self.out.state:
			# Wrong guess — the async method moved the automaton.
			# We need to regenerate the answer
			
			if not self.user.state.chat:
				# Same early exit as ever for a transition landing on
				# a state that doesn't accept chat at all — nothing
				# left to regenerate a reply for.
				self.session_manager.touch_session(self.user.session_id, self.out.state.key)

			self.out.reply = ""

			async for chunk in self.generate_reply(self.on_receiving_metadata_when_repeating_the_call):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		return self.out
