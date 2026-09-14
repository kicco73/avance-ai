from system.web_session import WebSession

from .tracking_processor import OutVariables, TrackingProcessor

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables(reply="", messages=[], tracking_id=None, state=self.user.state, action=None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata):
			self.out.reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		if not self.out.signals_resolved:
			self._resolve_signals({})

		if self._records_evaluation():
			self.out.tracking_id, written = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
				origin='trigger', username=WebSession().user, project_id=self.user.project_id,
				output_values=self.metadata.output,
			)
			self.out.env_changed.update(written)

		return self.out
