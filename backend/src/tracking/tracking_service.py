from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any, Callable

from automaton.automaton import Automaton, SignalPayload
from ai.ai_service import AiService
from project.project_service import ProjectService
from session import Session
from db import Db
from metrics.metric_service import MetricService

from .errors import TrackingServiceError
from .turn_callbacks import OnMetadata
from .env import PersistedEnv
from .evaluation_scope import EvaluationScopeBuilder
from .session_facts import SessionFacts
from .system_facts import SystemFacts
from .definitions import Signals
from .session_import import SessionImportManager
from .tracking_processor import UserVariables
from .tracking_processor_ai import TrackingProcessorAfterAiMessage
from .tracking_processor_user import TrackingProcessorAfterUserMessage

GetActiveAutomaton = Callable[[], Automaton]
GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]


class TrackingService(object):
	def __init__(
		self,
		db: Db,
		ai_service: AiService,
		project_service: ProjectService,
		metrics_service: MetricService,
	) -> None:
		self._db = db
		self._ai_service = ai_service
		self._project_service = project_service
		self._metrics = metrics_service
		self._session_import_manager = SessionImportManager(db)
		self.auto_tracking_enabled = True

	def import_session(self, username: str, project_name: str, text: str, title: str | None = None) -> int:
		try:
			return self._session_import_manager.import_transcript(username, project_name, text, title=title)
		except ValueError as exc:
			raise TrackingServiceError(str(exc), status_code=HTTPStatus.BAD_REQUEST) from exc

	@property
	def automaton(self) -> Automaton:
		automaton, _ = self._project_service.get_active_automaton_and_state()
		return automaton

	def get_definition(self, names: set[str] | None = None) -> str:
		signals = Signals(lambda: self.automaton, self._db)
		return signals.get_definition(names)

	def get_latest_signals(self) -> list[SignalPayload]:
		signals = Signals(lambda: self.automaton, self._db)
		return signals.get_latest_signals()

	def get_session_signals(self, session_id: int) -> list[dict]:
		return self._db.get_signals(session_id)

	def _require_annotatable_message(self, message_id: int) -> dict:
		row = self._db.get_signal_row_by_message(message_id)
		if row is None:
			row = self._materialize_session_start_row(message_id)
		if row is None:
			row = self._materialize_imported_session_row(message_id)
		if row is None:
			raise TrackingServiceError(
				"This message isn't an evaluation point — nothing to annotate.",
				status_code=HTTPStatus.CONFLICT,
			)
		return row

	def _materialize_imported_session_row(self, message_id: int) -> dict | None:
		"""An imported session (see ChatSession.source) never has any real
		Tracking rows at all — it was never played live through the
		automaton (see tracking.session_import's own module docstring), so
		unlike a live session's own first-message bootstrap (see
		_materialize_session_start_row), there's no old_state/new_state to
		resolve for it — every row this creates carries None for both.
		Only a message on whichever side automaton.autotracking_on_ai_message
		says a live turn would actually have evaluated on (the assistant's
		own reply if True, the user's own message if False — same
		convention a live session already follows, see TrackingService.
		process's own dispatch between TrackingProcessorAfterAiMessage/
		TrackingProcessorAfterUserMessage) is eligible to annotate at all —
		every other message of an imported session still has nothing to
		annotate against, same as before this existed."""
		message = self._db.get_message(message_id)
		if message is None:
			return None
		session = self._db.get_chat_session(message["session_id"])
		if session is None or session["source"] != "imported":
			return None
		expected_role = "assistant" if self.automaton.autotracking_on_ai_message else "user"
		if message["role"] != expected_role:
			return None
		self._db.save_transition(
			None, None, None, message["session_id"], transition_log_level="INFO", message_id=message_id
		)
		return self._db.get_signal_row_by_message(message_id)

	def _materialize_session_start_row(self, message_id: int) -> dict | None:
		"""Every session conceptually starts at its own `start_state`, but
		only the literal first session ever opened for a project gets a
		real Tracking row for that (see ChatService.open_if_needed's own
		"" -> start_state transition, created once per project, not once
		per session) — every other session's own start has nothing in the
		database to annotate against at all. That first session's own row
		is never linked to a message eagerly either (open_if_needed's own
		transition fires before any message of its own bootstrap exists),
		so both cases land here identically: lazily links (or, for every
		later session, creates then links) that row the first time an
		expert actually tries to annotate it. Returns None (falls through
		to the usual 409) for anything other than a session's own first
		message, or a session whose start row does exist but is linked
		elsewhere (must never happen in practice, but not this function's
		job to fix)."""
		message = self._db.get_message(message_id)
		if message is None:
			return None
		session_id = message["session_id"]
		earliest = self._db.get_messages(session_id)
		if not earliest or earliest[0]["id"] != message_id:
			return None
		existing = next(
			(row for row in self._db.get_signals(session_id) if row["old_state"] == ""), None
		)
		if existing is not None:
			if existing["message_id"] is not None:
				return None
			self._db.link_signal_to_message(existing["id"], message_id)
			return self._db.get_signal_row_by_message(message_id)
		session = self._db.get_chat_session(session_id)
		# old_state == "" specifically means "the automaton's own init
		# transition" elsewhere (see benchmarkTimeline.js's own
		# hasOwnStartRow/resolveTransitionRow) — an imported session (see
		# ChatSession.source) never actually ran through the automaton at
		# all, so it has no start_state to pair that with (see tracking.
		# session_import's own create_chat_session call, start_state=
		# None): writing ""->None here would claim a real init transition
		# happened when none did, rather than "nothing is known here" (see
		# _materialize_imported_session_row instead, which that falls
		# through to).
		if session is None or session["source"] == "imported":
			return None
		self._db.save_transition(
			"", "", session["start_state"], session_id, transition_log_level="INFO", message_id=message_id
		)
		return self._db.get_signal_row_by_message(message_id)

	def _finalize_annotation_write(self, signal_row_id: int, message_id: int) -> dict | None:
		"""Re-reads the row just written to — except a session-start
		bookkeeping row (old_state == "", see
		_materialize_session_start_row) left carrying no annotation at
		all afterward, which is deleted instead of kept around as an
		empty husk: it only ever existed to hold that annotation, so
		clearing the last one reverts things to exactly "no row exists
		for this message", same as before it was ever materialized.
		Returns None in that case — the caller (a PUT response) has
		nothing left to describe."""
		updated = self._db.get_signal_row_by_message(message_id)
		assert updated is not None  # just written above, under the same message
		if updated["old_state"] == "" and updated["expected_state"] is None and not updated["expected_values"]:
			self._db.delete_signal_row(signal_row_id)
			return None
		return updated

	def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
		"""Sets (expected_state given) or clears (None) the expert-
		annotated expected state for message_id's own evaluation — see
		Tracking.expected_state's own docstring. Returns the updated
		Tracking row, or None if clearing it deleted the row entirely (see
		_finalize_annotation_write). `expected_state` must name a real
		state in the active project's own automaton — the "Benchmark
		project" view's States dropdown is populated from exactly that
		list, but this is the one place that actually enforces it."""
		row = self._require_annotatable_message(message_id)
		if expected_state is not None:
			if expected_state == "" or expected_state not in self.automaton.states:
				raise TrackingServiceError(
					f"Unknown state '{expected_state}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
				)
		self._db.set_signal_expected_state(row["id"], expected_state)
		return self._finalize_annotation_write(row["id"], message_id)

	def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
		"""Sets or clears the expert-annotated expected signal values for
		message_id's own evaluation — see Tracking.expected_values's own
		docstring. `expected_values` is the *whole* replacement dict: a
		signal name missing from it is annotation-cleared for that signal
		alone (the "Label sessions" view's own sliders send the whole
		dict on every change, never a single-key patch). Every key must
		name a real signal in the active project, every value a plain
		number in [0, 100] (see Inspector.vue's own slider range). Returns
		the updated Tracking row, or None if clearing it deleted the row
		entirely (see _finalize_annotation_write)."""
		row = self._require_annotatable_message(message_id)
		if expected_values:
			valid_names = {s.name for s in self.automaton.signals}
			for name, value in expected_values.items():
				if name not in valid_names:
					raise TrackingServiceError(
						f"Unknown signal '{name}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
					)
				if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 100):
					raise TrackingServiceError(
						f"Signal '{name}' must be a number between 0 and 100.",
						status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
					)
		self._db.set_signal_expected_values(row["id"], expected_values)
		return self._finalize_annotation_write(row["id"], message_id)

	def clear_session_annotations(self, session_id: int) -> None:
		"""Clears every expert annotation (expected_state and
		expected_values alike) across session_id's own Tracking rows in
		one call — the "Label sessions" view's "Unlabel all" action.
		Ownership of `session_id` is the caller's own responsibility (see
		ChatService.clear_session_annotations)."""
		self._db.clear_session_annotations(session_id)

	def process(
		self,
		session_id: int,
		text: str | None,
		on_metadata: OnMetadata | None = None,
		extra_prompt: str | None = None,
		):

		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)

		user_vars = UserVariables(
			automaton=automaton,
			state=state,
			project_name=self._project_service.get_active_project_name(),
			session_id=session_id
		)

		if not automaton.autotracking_on_ai_message:
			TrackingProcessor = TrackingProcessorAfterUserMessage
		else:
			TrackingProcessor = TrackingProcessorAfterAiMessage

		get_username = lambda: Session().user
		get_active_project_name = self._project_service.get_active_project_name
		env = PersistedEnv(self._db, get_username=get_username, get_active_project_name=get_active_project_name)
		system_facts = SystemFacts()
		session_facts = SessionFacts(self._db, get_username=get_username, get_active_project_name=get_active_project_name)
		scope_builder = EvaluationScopeBuilder(env, self._metrics, system_facts, session_facts)

		def on_metadata_sync_to_async(key: str, value: Any):
			if on_metadata:
				asyncio.ensure_future(on_metadata(key, value))

		tracking_processor = TrackingProcessor(
			self._ai_service, scope_builder,
			env, self._db, user_vars,
			auto_tracking_enabled=self.auto_tracking_enabled,
		)

		return tracking_processor.process(text, on_metadata=on_metadata_sync_to_async, extra_prompt=extra_prompt)