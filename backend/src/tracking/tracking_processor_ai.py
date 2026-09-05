from session import Session

from .tracking_processor import OutVariables, TrackingProcessor

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables(reply="", messages=[], tracking_id=None, state=self.user.state, action=None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata):
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

		return self.out
