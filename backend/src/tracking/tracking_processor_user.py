from dataclasses import dataclass
from typing import Any

from automaton.automaton import State
from logging_factory import LoggerFactory
from session import Session
from tracking.tracking_processor import OutVariables, TrackingProcessor


logger = LoggerFactory.get_logger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):

	@dataclass
	class Parameters:
		signal_row_id: State

	def on_receiving_metadata_that_may_trigger_status_change(self, key: str, value: Any):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._tracking_engine.evaluate_triggered_action(
				self.user.automaton, self.user.state, self.metadata.signals
			)
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)
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
		self.metadata.on_metadata(key, rv)

	def on_receiving_metadata_when_repeating_the_call(self, key: str, value: Any):
		# 'signals' is never among tag_specs for this call — already known
		# from the first call, re-requesting them would be wasted and must
		# not trigger a second trigger evaluation. 'reaction' isn't among
		# them either currently (see _get_ai_reply's own tag_specs) — kept
		# here anyway so this handler stays a strict superset of what it's
		# actually called with, same as 'env'/'audio' above.
		rv = value
		if key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		elif key == 'reaction':
			rv = self.metadata.reaction = value.strip() or None
		elif key == 'input_tokens':
			rv = self.metadata.input_tokens = value
		elif key == 'output_tokens':
			rv = self.metadata.output_tokens = value
		self.metadata.on_metadata(key, rv)

	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)
		# By design, signals only ever arrive if the schema/tag set for this
		# turn actually asks for them — a message that doesn't request
		# 'signals' will never produce that metadata, so there's nothing to
		# gate text on: stream normally from the first chunk.
		self.out.signals_resolved = 'signals' not in self.build_turn_protocol().include_tags

		# Optimistic guess: generate the real reply first, using the
		# *current* state's own context — the common case (no transition)
		# needed exactly this one call anyway.

		buffered_text_before_signals_resolved = ""
		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_that_may_trigger_status_change):
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
			self._tracking_engine.apply_action_env(
				self.user.automaton, self.out.action, self.metadata.signals, self.user.state.key,
				username=Session().user, project_id=self.user.project_id, session_id=self.user.session_id,
			)

			# Signals are already known from the first call — asking again
			# would be wasted and must not trigger a second trigger
			# evaluation, so this regeneration only ever requests audio/text/env.
			base_prompt, chat_history = self._build_base_prompt_and_history(self.out.state)
			async for chunk in self.build_turn_protocol().generate_reply_with_schema(
				base_prompt, self.env,
				tag_specs=[('audio', 'audio'), ('text', 'text'), ('env', 'env')],
				chat_history=chat_history,
				on_metadata=self.on_receiving_metadata_when_repeating_the_call,
			):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		if self.metadata.signals:
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
