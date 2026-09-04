from typing import Any

from logging_factory import LoggerFactory
from session import Session

from .tracking_processor import OutVariables, TrackingProcessor

logger = LoggerFactory.get_logger(__name__)

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	def on_receiving_metadata_when_ai_message(self, key: str, value: Any):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._tracking_engine.evaluate_triggered_action(
				self.user.automaton, self.user.state, self.metadata.signals, session_id=self.user.session_id
			)
			self.out.signals_resolved = True

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		elif key == 'reaction':
			rv = self.metadata.reaction = value.strip() or None
		elif key == 'input_tokens':
			rv = self.metadata.input_tokens = value
		elif key == 'output_tokens':
			rv = self.metadata.output_tokens = value
		elif key == 'tool_result':
			self.metadata.tool_calls.append(value)
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables(reply="", messages=[], tracking_id=None, state=self.user.state, action=None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_when_ai_message):
			self.out.reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		if self.metadata.signals:
			# Never linked here (message_id omitted) — the assistant's own
			# message doesn't exist yet at this point. Called whenever
			# signals were evaluated, fired or not, so a no-op evaluation still leaves a real, queryable row.
			self.out.tracking_id = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
				origin='trigger', username=Session().user, project_id=self.user.project_id,
			)
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)

		return self.out
