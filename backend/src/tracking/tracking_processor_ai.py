from session import Session

from .tracking_processor import OutVariables, TrackingProcessor

class TrackingProcessorAfterAiMessage(TrackingProcessor):

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables(reply="", messages=[], tracking_id=None, state=self.user.state, action=None)

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata):
			self.out.reply += chunk
			self.metadata.on_metadata('chunk', chunk)

		if not self.out.signals_resolved:
			# Signals weren't requested this turn (nothing signal-backed to
			# ask for) — the state's own triggers are still evaluated, after
			# the reply as this strategy always does, against the empty
			# signals set (see TrackingProcessor._resolve_signals).
			self._resolve_signals({})

		if self._records_evaluation():
			# Never linked here (message_id omitted) — the assistant's own
			# message doesn't exist yet at this point. Called whenever the
			# model reported signals, fired or not, so a no-op evaluation
			# still leaves a real, queryable row.
			self.out.tracking_id = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
				origin='trigger', username=Session().user, project_id=self.user.project_id,
			)

		return self.out
