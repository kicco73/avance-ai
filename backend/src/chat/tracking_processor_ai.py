import logging

from automaton.automaton import Automaton, State

from .tracking_processor import OutVariables, TrackingProcessor

logger = logging.getLogger(__name__)

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	def on_receiving_metadata_when_ai_message(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._FIXME_would_trigger_action()

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_when_ai_message):
			self.out.reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		if self.out.action:
			self.out.tracking_id = self._FIXME_move_automaton()
			self.out.state = self.user.automaton.get_state(self.out.action.target)

		return self.out
