import asyncio
from dataclasses import dataclass
import logging

from automaton.automaton import Action, Automaton, State
from tracking.tracking_processor import OutVariables, TrackingProcessor


logger = logging.getLogger(__name__)

class TrackingProcessorAfterUserMessage(TrackingProcessor):

	@dataclass
	class Parameters:
		signal_row_id: State

	def on_receiving_metadata_that_may_trigger_status_change(self, key: str, value: str):
		rv = value
		if key == 'signals':
			rv = self.metadata.signals = self.metadata_processor.parse_raw_signals(value)
			self.out.action = self._tracking_engine.evaluate_triggered_action(
				self.user.automaton, self.user.state, self.metadata.signals
			)
			if self.out.action:
				self.out.state = self.user.automaton.get_state(self.out.action.target)
				# An auto-triggered action's own action-prompt used to only
				# ever fire for a *manually* clicked one (see ChatService.
				# apply_manual_action/_generate_action_prompt_message, which
				# runs it as its own dedicated turn) — silently dropped here
				# otherwise, even though the field exists on every action the
				# same way. self.extra_prompt is exactly the mechanism
				# __build_turn_prompt_parts already folds into whatever
				# reply gets generated next (see TrackingProcessor.process's
				# own docstring) — setting it here, the moment the fired
				# action is known, means the regeneration below (state
				# actually changed — see _get_ai_reply) already reads it
				# like any other. A fired *self-loop* still keeps its
				# already-streamed optimistic-guess reply verbatim (no
				# regeneration happens at all — see _get_ai_reply), so this
				# has no effect there: folding an action-prompt into an
				# already-completed stream isn't possible after the fact.
				if self.out.action.action_prompt:
					self.extra_prompt = self.out.action.action_prompt

		elif key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)

	def on_receiving_metadata_when_repeating_the_call(self, key: str, value: str):
		# 'signals' is never among tag_specs for this call anymore (see
		# _get_ai_reply's regeneration call) — already known from the
		# first call, re-requesting them would be wasted and must not
		# trigger a second trigger evaluation.
		rv = value
		if key == 'env':
			rv = self.metadata.env = self.metadata_processor.parse_raw_env(value)
		elif key == 'audio':
			rv = self.metadata.audio = value
		self.metadata.on_metadata(key, rv)
	
	async def _get_ai_reply(self) -> OutVariables:

		self.out = OutVariables("", [], None, self.user.state, None)

		# Optimistic guess: generate the real reply first, using the
		# *current* state's own context (see this module's own
		# docstring) — the common case (no transition) needed exactly
		# this one call anyway.

		async for chunk in self.generate_reply(self.user.state, self.on_receiving_metadata_that_may_trigger_status_change):
			if self.user.state == self.out.state:
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		if self.metadata.signals:
			# This "before" mode's own trigger is decided from the user's
			# message (already saved — see self.user.message_id), not from
			# the assistant's reply that's about to be (maybe) regenerated
			# below — so the row must be linked to the user's message right
			# away, rather than left for process()'s own post-hoc link to
			# the (causally unrelated) assistant message. Called whenever
			# signals were evaluated at all, fired or not — apply_transition
			# itself saves a plain snapshot when self.out.action is still
			# None (see tracking_engine.py), so an evaluation that didn't
			# trigger anything (e.g. the opening message's own) still
			# leaves a real, queryable row instead of vanishing outright.
			#
			# An opening turn (has_ai_started_conversation) has no real
			# user message of its own though — self.user.message_id only
			# points at a placeholder ('...') that process() deletes right
			# after this returns, which would silently orphan an early
			# link to it (Tracking.message is ON DELETE SET NULL). Left
			# unlinked here instead, same as "after" mode always is, so
			# process()'s own post-hoc link attaches it to the one real
			# message this turn actually produces (its own reply).
			has_real_user_message = not self.user.has_ai_started_conversation
			self.out.tracking_id = self._tracking_engine.apply_transition(
				self.user.automaton, self.user.state, self.out.action, self.metadata.signals, self.user.session_id,
				message_id=self.user.message_id if has_real_user_message else None,
			)
			self.out.tracking_linked_to_message = has_real_user_message

		if self.user.state != self.out.state:
			# Wrong guess — the async method moved the automaton.
			# We need to regenerate the answer

			self.out.reply = ""
			self.metadata.on_metadata('text', "")

			# Signals are already known from the first call — asking
			# again here would be wasted (and must not trigger a second
			# trigger evaluation, see on_receiving_metadata_when_
			# repeating_the_call, which never handles 'signals'), so
			# this regeneration only ever requests audio/text/env.
			base_prompt, chat_history = self._build_base_prompt_and_history(self.out.state)
			async for chunk in self.build_turn_protocol().generate_reply_with_schema(
				base_prompt,
				tag_specs=[('audio', 'audio'), ('text', 'text'), ('env', 'env')],
				chat_history=chat_history,
				on_metadata=self.on_receiving_metadata_when_repeating_the_call,
			):
				self.out.reply += chunk
				self.metadata.on_metadata('chunk', chunk)

		return self.out
