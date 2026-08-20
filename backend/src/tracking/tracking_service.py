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

from .automaton_namespace import AutomatonNamespace
from .errors import TrackingServiceError
from .turn_callbacks import OnMetadata
from .env import PersistedEnv
from .evaluation_scope import EvaluationScopeBuilder
from .session_facts import SessionFacts
from .system_facts import SystemFacts
from .definitions import Signals
from .session_import import SessionImportManager
from .session_export import SessionExportManager
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
		self._session_export_manager = SessionExportManager(db)
		# "Dev mode: freeze automatic state transitions" toggle — per
		# 'test' session, never global: a native/imported session is
		# always auto-tracked. Absent = enabled.
		self._disabled_test_sessions: set[int] = set()

	def is_auto_tracking_enabled(self, session_id: int) -> bool:
		return session_id not in self._disabled_test_sessions

	def set_auto_tracking_enabled(self, session_id: int, enabled: bool) -> None:
		if enabled:
			self._disabled_test_sessions.discard(session_id)
		else:
			self._disabled_test_sessions.add(session_id)

	def clear_auto_tracking_overrides(self) -> None:
		"""A full DB restore can reuse session ids the in-memory freeze
		set still refers to — clears it outright rather than risk a stale
		entry silently freezing an unrelated, newly-restored session."""
		self._disabled_test_sessions.clear()

	def import_session(self, username: str, project_name: str, text: str, title: str | None = None) -> int:
		try:
			return self._session_import_manager.import_transcript(username, project_name, text, title=title)
		except ValueError as exc:
			raise TrackingServiceError(str(exc), status_code=HTTPStatus.BAD_REQUEST) from exc

	def import_session_json(self, username: str, project_name: str, session_data: dict) -> int:
		"""The "Label sessions" view's own JSON upload. Same "malformed
		input is a 400, never a 500" convention as import_session above,
		just a wider exception set for a hand-edited/corrupted JSON file."""
		try:
			return self._session_import_manager.import_session_json(username, project_name, session_data)
		except (ValueError, KeyError, TypeError) as exc:
			raise TrackingServiceError(f"Invalid session data: {exc}", status_code=HTTPStatus.BAD_REQUEST) from exc

	def export_sessions(self, username: str, project_name: str) -> list[dict]:
		"""The "Label sessions" view's own "Download all" button — see
		SessionExportManager.export_sessions."""
		return self._session_export_manager.export_sessions(username, project_name)

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
		"""An imported session never has any real Tracking rows — it was
		never played live through the automaton, so every row this creates
		carries None for old_state/new_state. Every message is a legitimate mark point for an expert to annotate."""
		message = self._db.get_message(message_id)
		if message is None:
			return None
		session = self._db.get_chat_session(message["session_id"])
		if session is None or session["source"] != "imported":
			return None
		self._db.save_transition(
			None, None, None, message["session_id"], transition_log_level="INFO", message_id=message_id
		)
		return self._db.get_signal_row_by_message(message_id)

	def _materialize_session_start_row(self, message_id: int) -> dict | None:
		"""Only the literal first session ever opened for a project gets a
		real Tracking row for its start_state transition — every other
		session's start has nothing in the DB, so this lazily links (or
		creates then links) that row the first time an expert annotates it."""
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
		# transition" — an imported session never ran through the automaton
		# at all, so writing ""->None here would falsely claim one happened.
		if session is None or session["source"] == "imported":
			return None
		self._db.save_transition(
			"", "", session["start_state"], session_id, transition_log_level="INFO", message_id=message_id
		)
		return self._db.get_signal_row_by_message(message_id)

	def _finalize_annotation_write(self, signal_row_id: int, message_id: int) -> dict | None:
		"""Re-reads the row just written to — except a session-start
		bookkeeping row left carrying no annotation at all, which is
		deleted instead of kept as an empty husk. Returns None in that case."""
		updated = self._db.get_signal_row_by_message(message_id)
		assert updated is not None  # just written above, under the same message
		if updated["old_state"] == "" and updated["expected_state"] is None and not updated["expected_values"]:
			self._db.delete_signal_row(signal_row_id)
			return None
		return updated

	def _automaton_for_message(self, message_id: int) -> Automaton:
		"""message_id's own session's own project's Automaton — never
		self.automaton (the active project's), which would silently
		validate against the wrong project whenever message_id belongs to a project that isn't the one currently active."""
		message = self._db.get_message(message_id)
		assert message is not None  # _require_annotatable_message already confirmed this above
		automaton, _ = self._project_service.get_automaton_and_state_for_session(message["session_id"])
		return automaton

	def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
		"""Sets or clears the expert-annotated expected state for
		message_id. Returns the updated row, or None if clearing it
		deleted the row entirely. `expected_state` must name a real state — this is the one place that actually enforces it."""
		row = self._require_annotatable_message(message_id)
		if expected_state is not None:
			if expected_state == "" or expected_state not in self._automaton_for_message(message_id).states:
				raise TrackingServiceError(
					f"Unknown state '{expected_state}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
				)
		self._db.set_signal_expected_state(row["id"], expected_state)
		return self._finalize_annotation_write(row["id"], message_id)

	def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
		"""Sets or clears the expert-annotated expected signal values.
		`expected_values` is the *whole* replacement dict — a signal name
		missing from it is annotation-cleared, never a single-key patch. Every value must be a number in [0, 100]."""
		row = self._require_annotatable_message(message_id)
		if expected_values:
			valid_names = {s.name for s in self._automaton_for_message(message_id).signals}
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

	def _require_commentable_message(self, message_id: int) -> dict:
		"""Resolves message_id to its own Tracking row — unlike
		_require_annotatable_message, never raises: a comment can be left
		on *any* chat line, so a message with nothing else to annotate still gets a bare row here purely to hold it."""
		row = self._db.get_signal_row_by_message(message_id)
		if row is not None:
			return row
		message = self._db.get_message(message_id)
		assert message is not None  # ownership/existence already checked by ChatService._require_own_message
		self._db.save_transition(
			None, None, None, message["session_id"], transition_log_level="INFO", message_id=message_id
		)
		row = self._db.get_signal_row_by_message(message_id)
		assert row is not None  # just written above, under the same message
		return row

	def set_message_comment(self, message_id: int, comment: str | None) -> dict | None:
		"""Sets or clears the expert-left comment for message_id. Unlike
		set_message_expected_state/set_message_expected_signals, this
		never deletes the row on clearing — no safe way to tell if another write (e.g. set_env) is relying on it."""
		comment = comment.strip() if comment else None
		row = self._require_commentable_message(message_id)
		self._db.set_signal_comment(row["id"], comment or None)
		return self._db.get_signal_row_by_message(message_id)

	def clear_session_annotations(self, session_id: int) -> None:
		"""Clears every expert annotation (expected_state and
		expected_values) across session_id's Tracking rows in one call —
		the "Label sessions" view's "Unlabel all" action."""
		self._db.clear_session_annotations(session_id)

	def process(
		self,
		session_id: int,
		text: str | None,
		on_metadata: OnMetadata | None = None,
		extra_prompt: str | None = None,
		):

		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)
		session = self._db.get_chat_session(session_id)
		is_test_session = session is not None and session["source"] == "test"

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
		automaton_namespace = AutomatonNamespace(self._db, self._project_service, get_username)
		scope_builder = EvaluationScopeBuilder(env, self._metrics, system_facts, session_facts, automaton_namespace)

		def on_metadata_sync_to_async(key: str, value: Any):
			if on_metadata:
				asyncio.ensure_future(on_metadata(key, value))

		tracking_processor = TrackingProcessor(
			self._ai_service, scope_builder,
			env, self._db, user_vars,
			auto_tracking_enabled=self.is_auto_tracking_enabled(session_id) if is_test_session else True,
		)

		return tracking_processor.process(text, on_metadata=on_metadata_sync_to_async, extra_prompt=extra_prompt)