import logging

from automaton.automaton import Automaton, State

from .tracking_processor import OutVariables, TrackingProcessor

logger = logging.getLogger(__name__)

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	def on_receiving_metadata_when_ai_message(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		reply = ""
		async for chunk in self.generate_reply(self.on_receiving_metadata_when_ai_message):
			reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		action, state, transition_messages, tracking_id = await self._run_auto_tracking()

		return OutVariables(reply, transition_messages, tracking_id, state, action)
