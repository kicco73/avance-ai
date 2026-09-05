from dataclasses import dataclass

from automaton.automaton import State
from logging_factory import LoggerFactory
from session import Session
from tracking.tracking_processor import OutVariables, TrackingProcessor
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema


logger = LoggerFactory.get_logger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):

	@dataclass
	class Parameters:
		signal_row_id: State

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)
		if not self._evaluate_signals_for(self.user.state):
			# Nothing signal-backed to ask the model for this turn — the
			# state's own triggers are still evaluated, right now, against
			# the empty signals set (a metric.*/env.*/source.* trigger fires
			# exactly as it always did; a signal-backed one short-circuits
			# to false): the gate switches off the request, never the
			# evaluation. The outcome is known before the first chunk, so
			# there's nothing to buffer text for either way.
			self._resolve_signals({})

		buffered_text_before_signals_resolved = ""
		if self.user.state == self.out.state:
			# Optimistic guess: generate the real reply first, using the
			# *current* state's own context — the common case (no
			# transition) needed exactly this one call anyway. Skipped
			# outright when the upfront evaluation above already moved the
			# automaton: that reply would only ever be discarded below.
			async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata):
				if not self.out.signals_resolved:
					buffered_text_before_signals_resolved += chunk
				elif self.user.state == self.out.state:
					if not self.out.reply:
						chunk = buffered_text_before_signals_resolved + chunk
					self.out.reply += chunk
					self.metadata.on_metadata('chunk', chunk)

		if not self.out.signals_resolved and buffered_text_before_signals_resolved:
			# Safety net: the model never produced a 'signals' tag/field at
			# all (malformed output, schema not honored, ...) — without this
			# the whole buffered reply would be silently lost, since nothing
			# else ever flushes it into self.out.reply or on to the client.
			self.out.reply = buffered_text_before_signals_resolved
			self.metadata.on_metadata('chunk', buffered_text_before_signals_resolved)

		transitioned = self.user.state != self.out.state

		if transitioned:
			# Wrong guess — the async method moved the automaton.
			# We need to regenerate the answer

			self.out.reply = ""

			# Must run before the regenerated prompt below (the action's
			# env: writes feed it), and not again via apply_transition
			# further down — see record_transition. The action's on-enter
			# is scheduled as a task from here, once.
			#
			# Note: with signal-tracking-on-ai-message false (the case that
			# actually reaches this branch), any avance:env `update` tool
			# call the optimistic reply above already made is a real,
			# persisted write the instant it happened — it's never rolled
			# back just because that reply itself gets discarded here for a
			# transition. The regeneration below, running in the new state,
			# sees it as the current value like any other action_set write.
			self._tracking_engine.apply_action_env(
				self.user.automaton, self.out.action, self.metadata.signals, self.user.state.key,
				username=Session().user, project_id=self.user.project_id, session_id=self.user.session_id,
			)

			# Signals are already known from the first call — asking again
			# would be wasted and must not trigger a second trigger
			# evaluation, so this regeneration only ever requests audio/text/memory.
			base_prompt, chat_history, env_block = self._build_base_prompt_and_history(self.out.state)
			channels = self.build_regeneration_channels(self.out.state, base_prompt)
			async for chunk in TurnProtocolUsingSchema(self.ai_service).generate_reply(
				channels, chat_history, on_metadata=self.on_receiving_metadata,
				tool_set=self.build_tool_set(self.out.state),
				force_required_tools=self.force_required_tools_for(self.out.state),
				env_block=env_block.text() if env_block else None,
			):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		if self._records_evaluation():
			# The trigger is decided from the user's message, so this row
			# links to it directly — except an opening turn, whose
			# message_id only points at a placeholder that gets deleted, which would silently orphan an early link.
			has_real_user_message = not self.user.has_ai_started_conversation
			if transitioned:
				self.out.tracking_id = self._tracking_engine.record_transition(
					self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
					message_id=self.user.message_id if has_real_user_message else None,
					origin='trigger', username=Session().user, project_id=self.user.project_id,
				)
			else:
				self.out.tracking_id = self._tracking_engine.apply_transition(
					self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
					message_id=self.user.message_id if has_real_user_message else None,
					origin='trigger', username=Session().user, project_id=self.user.project_id,
				)
			self.out.tracking_linked_to_message = has_real_user_message

		return self.out
