import logging

from .tracking_processor import OutVariables, TrackingProcessor

logger = logging.getLogger(__name__)

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	def on_receiving_metadata_when_ai_message(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._tracking_engine.evaluate_triggered_action(
				self.user.automaton, self.user.state, self.metadata.signals
			)

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables(reply="", messages=[], tracking_id=None, state=self.user.state, action=None)
		("", [], None, self.user.state, None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_when_ai_message):
			self.out.reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		if self.metadata.signals:
			# Never linked here (message_id omitted) — this mode's own
			# evaluation reads the assistant's own reply, whose message
			# doesn't exist yet at this point (see TrackingProcessor.
			# process, which creates it right after _get_ai_reply returns
			# and links this row to it then). Called whenever signals were
			# evaluated at all, fired or not — apply_transition itself
			# saves a plain snapshot when self.out.action is still None
			# (see tracking_engine.py), so an evaluation that didn't
			# trigger anything still leaves a real, queryable row instead
			# of vanishing outright.
			self.out.tracking_id = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id
			)
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)

		return self.out
