from automaton.choice import ChoiceSelection
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from .tracking_processor import OutVariables, TrackingProcessor


logger = LoggerFactory.get_logger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):
	# XXX FOR CLAUDE CODE: this class never reaches the Db. A new method that

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)
		if not self._evaluate_signals_for(self.user.state):
			if self.user.has_ai_started_conversation:
				self.metadata.signals = None
				self.out.signals_resolved = True
			else:
				self._resolve_signals(None)

		buffered_text_before_signals_resolved = ""
		if self.user.state == self.out.state:
			async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata):
				if not self.out.signals_resolved:
					buffered_text_before_signals_resolved += chunk
				elif self.user.state == self.out.state:
					if not self.out.reply:
						chunk = buffered_text_before_signals_resolved + chunk
					self.out.reply += chunk
					self.metadata.on_metadata('chunk', chunk)

		if not self.out.reply and buffered_text_before_signals_resolved and self.user.state == self.out.state:
			self.out.reply = buffered_text_before_signals_resolved
			self.metadata.on_metadata('chunk', buffered_text_before_signals_resolved)

		self._apply_output_to_env(self.user.state)
		transitioned = self.user.state != self.out.state
		self.moved_before_reply = transitioned

		if transitioned:

			self.out.reply = ""
			self.out.env_changed.update(self._tracking_engine.apply_action_env(
				self.user.automaton, self.out.action, self._signal_scope(), ChoiceSelection.NONE, self.user.state.key,
				username=WebSession().user, project_id=self.user.project_id, session_id=self.user.session_id,
				output_values=self.metadata.output,
			))
			async for chunk in self.reply_after_transition(self.out.state, self.on_receiving_metadata):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)
			self._apply_output_to_env(self.out.state)

		if self._records_evaluation():
			has_real_user_message = not self.user.has_ai_started_conversation
			if transitioned:
				self.out.tracking_id = self._tracking_engine.record_transition(
					self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
					message_id=self.user.message_id if has_real_user_message else None,
					origin='trigger', username=WebSession().user, project_id=self.user.project_id,
					output_values=self.metadata.output,
				)
			else:
				self.out.tracking_id, written = self._tracking_engine.apply_transition(
					self.user.automaton, self.user.state, self.out.action, self.metadata.signals, ChoiceSelection.NONE,
					self.user.session_id,
					message_id=self.user.message_id if has_real_user_message else None,
					origin='trigger', username=WebSession().user, project_id=self.user.project_id,
					output_values=self.metadata.output, scope_signals=self._signal_scope(),
				)
				self.out.env_changed.update(written)
			self.out.tracking_linked_to_message = has_real_user_message

		return self.out
