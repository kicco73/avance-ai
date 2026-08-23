from __future__ import annotations

import asyncio
import logging

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, SignalPayload, State
from db import Db, _utc_iso
from ai.ai_service import AiService
from keyed_lock_registry import KeyedLockRegistry
from project_rw_lock import ProjectRwLock
from session import Session

from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from chat.errors import ChatServiceError
from chat.session_manager import ChatSessionManager
from chat.session_summary_manager import SessionSummaryManager
from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from jobs import JobQueue
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.turn_callbacks import OnMetadata
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

logger = logging.getLogger(__name__)

class ChatService(object):
	def __init__(
		self,
		db: Db,
		ai_service: AiService,
		project_service: ProjectService,
		session_manager: ChatSessionManager,
		tracking_service: TrackingService,
		metric_service: MetricService,
		persisted_jobs: JobQueue,
	) -> None:
		self._db = db
		self._ai_service = ai_service
		self._project_service = project_service
		self._session_manager = session_manager
		self._tracking_service = tracking_service
		self.metric_service = metric_service
		# Shares persisted_jobs with BenchmarkRunService (see main.py's own
		# wiring) — never its own private queue.
		self._session_summary_manager = SessionSummaryManager(db, ai_service, persisted_jobs, session_manager)
		self.env = PersistedEnv(db, project_service)
		self._system_facts = SystemFacts()
		self._session_facts = SessionFacts(db, project_service)
		self._automaton_namespace = AutomatonNamespace(db, project_service)
		self._evaluation_scope_builder = EvaluationScopeBuilder(
			self.env, metric_service, self._system_facts, self._session_facts, self._automaton_namespace
		)
		self._tracking_engine = TrackingEngine(DbTrackingSink(db), self.env, self._evaluation_scope_builder)

		self._project_locks = KeyedLockRegistry(ProjectRwLock)
		self._session_locks = KeyedLockRegistry(asyncio.Lock)
		self._global_lock = asyncio.Lock()


	@property
	def _active_project_name(self) -> str:
		return self._project_service.get_active_project_name()

	@property
	def _username(self) -> str:
		return Session().user

	def get_message_audio_text(self, message_id: int) -> str | None:
		return self._db.get_message_audio_text(message_id)

	def get_session_summary(self, session_id: int) -> dict:
		"""{content: str | None} — None whether no SessionSummary row
		exists yet or one does but its job hasn't completed. Only ever
		answers "is a summary ready", never which of those two it is."""
		summary = self._db.get_session_summary(session_id)
		return {'content': summary['content'] if summary is not None else None}

	def get_ai_models_info(self) -> dict:
		return self._ai_service.get_models_info()

	def select_ai_model(self, index: int | None) -> None:
		self._ai_service.select_model(index)

	@staticmethod
	def _now_iso() -> str:
		return datetime.now(timezone.utc).isoformat()

	@staticmethod
	def _strip_timestamps(history: list[dict]) -> list[dict]:
		"""`LLMProvider.generate` only knows {role, content} — timestamps are
		kept in the persisted conversation for /api/signals, not sent to the
		model during normal chat."""
		return [{"role": m["role"], "content": m["content"]} for m in history]

	def _session_payload(self, session: dict, *, active: bool) -> dict:
		return {
			"id": session["id"],
			"username": session["username"],
			"project_name": session["project_name"],
			"type": session["type"],
			"title": session["title"],
			"datetime_start": _utc_iso(session["datetime_start"]),
			"datetime_end": _utc_iso(session["datetime_end"]),
			"start_state": session["start_state"],
			"end_state": session["end_state"],
			# An imported session has no datetime_end — never a live
			# conversation window, so it's never "open".
			"open": self._session_manager.is_open(session),
			# Distinct from "open": the single open session with the most
			# recent datetime_start for this project — whether it still
			# accepts chat turns/manual actions, never computed client-side.
			"active": active,
			# A domain expert's explicit, persisted verdict — the "Label
			# sessions" view's Sessions panel marker and "Mark done" state.
			"has_annotations": session["labeled"],
			# A domain expert's free-text note on the session as a whole —
			# the "Label sessions" view's Info tab.
			"comment": session["comment"],
		}

	def _require_active_session(self, session_id: int | None, project_name: str, current_state: str) -> dict:
		"""A chat turn's session must already be the active one for this
		project — rejected just as firmly if merely open-but-superseded
		as if outright closed. ValueError becomes a 409 the frontend can act on."""
		try:
			return self._session_manager.require_active_session(
				self._username, project_name, session_id, current_state
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc

	def _resolve_or_create_session_of_type(
		self, strategy: SessionTypeStrategy, project_name: str, session_id: int | None
	) -> dict:
		try:
			# No active session means a new one is about to be created —
			# the moment the previously active one (if any) is discovered closed.
			if strategy.type_name == 'live' and self._session_manager.get_active_session(self._username, project_name) is None:
				self._session_summary_manager.check_for_closed_sessions(self._username, project_name)
			automaton, state = self._project_service.get_automaton_and_state(project_name, type=strategy.type_name)
			session = self._session_manager.resolve_or_create_session(
				strategy, self._project_service, self._username, project_name, session_id, automaton, state.key
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
		return {**self._session_payload(session, active=True), "state": automaton.get_state_payload(state)}

	def get_or_create_current_session(self, session_id: int | None) -> dict:
		"""Bootstrap for a client with no (or a possibly-stale) session_id:
		resolves or creates the one session currently writable for the
		active project. A paused project skips bootstrapping entirely."""
		project_name = self._active_project_name
		is_paused, paused_reason = self._project_service.get_project_availability(project_name)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		return self._resolve_or_create_session_of_type(get_session_type_strategy('live'), project_name, session_id)

	def get_or_create_current_draft_session(self, session_id: int | None, project_name: str) -> dict:
		"""Like get_or_create_current_session, but for `project_name`'s own
		*draft* — the embedded "Test" chat is the one place a session is
		allowed to exist against an unpublished revision. `project_name`
		comes from the URL, never the active-project pointer."""
		return self._resolve_or_create_session_of_type(get_session_type_strategy('test'), project_name, session_id)

	def _create_session_of_type(self, strategy: SessionTypeStrategy, project_name: str) -> dict:
		try:
			automaton, state = self._project_service.get_automaton_and_state(project_name, type=strategy.type_name)
			current_state = state.key if strategy.type_name == 'live' else None
			session = self._session_manager.create_session(
				strategy, self._project_service, self._username, project_name, automaton, current_state
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		# "on-enter": a new session enters its starting state *through*
		# init_action itself — the same wire key every other real
		# transition reports, just for init_action instead of a regular Action.
		return {**self._session_payload(session, active=True), "on-enter": automaton.init_action.on_enter}

	def create_session(self) -> dict:
		"""Explicit "new session" action: starts a fresh session, which
		immediately becomes the active project's writable one, recorded as
		starting at the automaton's current state — wherever the shared
		position sits right now, not necessarily the initial one."""
		return self._create_session_of_type(get_session_type_strategy('live'), self._active_project_name)

	def create_draft_session(self, project_name: str) -> dict:
		"""Like create_session, but against `project_name`'s own current
		*draft* revision, always starting fresh from the automaton's own
		initial state — the embedded "Test" chat is the only caller.
		`project_name` comes from the URL, never the active-project pointer."""
		return self._create_session_of_type(get_session_type_strategy('test'), project_name)

	def reset_test_sessions(self, project_name: str) -> dict:
		self._project_service.reset_test_sessions(project_name)
		automaton, state = self._project_service.get_automaton_and_state(project_name, type='test')
		return {**automaton.get_state_payload(state), "on-enter": automaton.init_action.on_enter}

	def _is_session_active(self, session: dict, pool_active_id: int | None) -> bool:
		fixed = get_session_type_strategy(session["type"]).default_active()
		return fixed if fixed is not None else session["id"] == pool_active_id

	def _list_sessions_by_type(self, project_name: str, type: str | tuple[str, ...], active_type: str) -> list[dict]:
		sessions = self._db.list_chat_sessions(self._username, project_name, type=type)
		active = self._session_manager.get_active_session(self._username, project_name, type=active_type)
		active_id = active["id"] if active is not None else None
		return [self._session_payload(s, active=self._is_session_active(s, active_id)) for s in sessions]

	def list_sessions(self, project_name: str, include_imported: bool = False) -> list[dict]:
		"""Every real (live, and optionally imported) session for
		`project_name`, most recently started first — for the "Sessions"
		panel. Never a 'test' session (see list_test_sessions instead)."""
		type = ('live', 'imported') if include_imported else 'live'
		return self._list_sessions_by_type(project_name, type, active_type='live')

	def list_test_sessions(self, project_name: str) -> list[dict]:
		"""Like list_sessions, but `project_name`'s own 'test' sessions —
		live/imported never appear here, symmetric to how a test session
		never appears in list_sessions."""
		return self._list_sessions_by_type(project_name, 'test', active_type='test')

	def _require_own_session(self, session_id: int) -> None:
		"""Raises (404) unless `session_id` still exists and belongs to
		the current user — sessions can be deleted independently, so a
		caller about to write to one can't just assume it's still there."""
		session = self._db.get_chat_session(session_id)
		if session is None or session["username"] != self._username:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)

	def delete_session(self, session_id: int) -> None:
		"""Deletes `session_id` and everything scoped to it (see
		db.delete_chat_session) — only the current user's own sessions,
		never someone else's by guessing an id."""
		self._require_own_session(session_id)
		self._db.delete_chat_session(session_id)

	def _reloaded_session_payload(self, session_id: int) -> dict:
		"""Common tail for every "write one field, then hand back a fresh
		payload" mutation below."""
		session = self._db.get_chat_session(session_id)
		assert session is not None
		fixed = get_session_type_strategy(session["type"]).default_active()
		if fixed is not None:
			active = fixed
		else:
			resolved = self._session_manager.get_active_session(self._username, session["project_name"], type=session["type"])
			active = resolved is not None and resolved["id"] == session_id
		return self._session_payload(session, active=active)

	def set_session_title(self, session_id: int, title: str | None) -> dict:
		"""The "Label sessions" view's Info tab — a domain expert's rename
		for a session, editable regardless of source. Blank/whitespace-only collapses to None."""
		self._require_own_session(session_id)
		stripped = title.strip() if title is not None else None
		self._db.set_session_title(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def set_session_comment(self, session_id: int, comment: str | None) -> dict:
		"""The "Label sessions" view's Info tab — a domain expert's
		free-text note on the session as a whole, distinct from a
		per-message comment. Same blank-collapses-to-None convention as set_session_title."""
		self._require_own_session(session_id)
		stripped = comment.strip() if comment is not None else None
		self._db.set_session_comment(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def mark_session_labeled(self, session_id: int, labeled: bool) -> dict:
		"""The "Label sessions" view's "Mark done" button — a domain
		expert's explicit confirmation that this session's been reviewed.
		A toggle: `labeled=False` un-marks it again."""
		self._require_own_session(session_id)
		self._db.set_session_labeled(session_id, labeled)
		return self._reloaded_session_payload(session_id)

	async def truncate_session(self, session_id: int, timestamp: str) -> None:
		""""Restart from here": deletes every message/signal at or after
		`timestamp` in `session_id`; the live automaton state is just
		recomputed from whatever Tracking rows survive."""
		self._require_own_session(session_id)
		project_name = self._project_name_for_session(session_id)
		async with self._session_scope(project_name, session_id):
			cutoff = datetime.fromisoformat(timestamp).replace(tzinfo=None)
			self._db.truncate_session(session_id, cutoff)
			session = self._db.get_chat_session(session_id)
			assert session is not None
			latest = self._db.latest_message_or_signal_timestamp(session_id)
			_, state = self._project_service.get_automaton_and_state_for_session(session_id)
			self._db.touch_chat_session(session_id, latest or session["datetime_start"], state.key)

	def get_state_for_session(self, session_id: int) -> dict:
		self._require_own_session(session_id)
		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)
		return automaton.get_state_payload(state)

	async def get_messages(self, session_id: int, last_n: int | None = None) -> list[dict]:
		# Checked before open_if_needed (which can write an opening
		# message): a session can be deleted out from under a stale
		# request — fail clean instead of an IntegrityError in save_message.
		self._require_own_session(session_id)
		# init_action's own message (if any) is deliberately never
		# persisted — this is the only place it's surfaced.
		init_message = await self.open_if_needed(session_id)
		messages = self._db.get_messages(session_id, last_n=last_n)
		if init_message is not None:
			messages.insert(0, init_message)
		return messages

	def get_session_signals(self, session_id: int) -> list[dict]:
		"""The full Tracking event log for `session_id` — every
		snapshot/transition row, chronological — for the "Label sessions"
		view's timeline, reconstructed entirely client-side."""
		self._require_own_session(session_id)
		return self._tracking_service.get_session_signals(session_id)

	def _require_own_message(self, message_id: int) -> dict:
		"""Raises (404) unless `message_id` exists and belongs to a
		session owned by the current user — message-scoped _require_own_session."""
		message = self._db.get_message(message_id)
		if message is not None:
			session = self._db.get_chat_session(message["session_id"])
			if session is not None and session["username"] == self._username:
				return message
		raise ChatServiceError("Message not found.", status_code=HTTPStatus.NOT_FOUND)

	def _until_from_message(self, message_id: int | None) -> datetime | None:
		"""Resolves an optional message_id into a naive-UTC cutoff
		timestamp, keyed by message id so the UI never has to serialize/
		parse a raw timestamp itself. None (live/current) when message_id is None."""
		if message_id is None:
			return None
		message = self._require_own_message(message_id)
		# Stored DateTimeField columns are naive-but-really-UTC, so the
		# tzinfo must come back off before comparing against one, or
		# Python raises on naive-vs-aware comparison.
		return datetime.fromisoformat(message["timestamp"]).replace(tzinfo=None)

	def get_metrics(
		self, project_name: str, message_id: int | None = None, full: bool = False, username: str | None = None,
	) -> list[dict]:
		"""metrics.metrics_framework's core metrics for `project_name` —
		the full current history, or (`message_id` given) restricted to
		what existed at or before that message's own timestamp. `full`:
		every core metric, not just the "one_session" subset — see
		MetricService.calculate_all's own `include_all_scopes`. `username`
		omitted means the caller's own sessions — see its own `username`."""
		until = self._until_from_message(message_id)
		return self.metric_service.calculate_all(
			until=until, project_name=project_name, include_all_scopes=full, username=username,
		)

	def preview_triggers(self, signals: dict) -> list:
		automaton, state = self._project_service.get_active_automaton_and_state()
		scope = self._evaluation_scope_builder.build(automaton, state.key, signals)
		return automaton.preview_triggers(state.key, scope)

	def get_env(self, message_id: int | None = None) -> dict:
		"""{"stored": ..., "action_set": ...}, reported separately so the
		Inspector Env tab knows which section each value belongs in and
		which are editable. Live, or as of `message_id` if given."""
		until = self._until_from_message(message_id)
		return {
			"stored": self.env.stored(until),
			"action_set": self.env.action_set(until),
		}

	def set_env_value(self, key: str, value: str) -> dict:
		"""Edits one stored env key — always live, no "editing history".
		A direct human edit can happen before any turn ever ran, so this
		bootstraps a session first since db.Db.set_env is a no-op without one."""
		self.get_or_create_current_session(None)
		self.env.set_value(key, value)
		return self.get_env()

	def delete_env_key(self, key: str) -> dict:
		"""Removes one stored env key outright (see chat.env.Env.
		delete_key) — always live. Returns the same shape as get_env."""
		self.get_or_create_current_session(None)
		self.env.delete_key(key)
		return self.get_env()

	def clear_env(self) -> dict:
		"""Wipes every stored env key at once (see chat.env.Env.clear) —
		the Inspector Env tab's own "clear all" button for the AI
		section. Always live. Returns the same shape as get_env."""
		self.get_or_create_current_session(None)
		self.env.clear()
		return self.get_env()

	def clear_action_env(self) -> dict:
		"""Wipes every action-set env key at once — the Inspector Env
		tab's "clear all" button for the ACTION section. Always live."""
		self.get_or_create_current_session(None)
		self.env.clear_action_set()
		return self.get_env()

	def get_benchmark_metrics(self, project_name: str, session_id: int | None = None) -> list[dict]:
		"""Expert-annotation-vs-actual benchmark metrics for `project_name`
		— every annotated session, or (session_id given) just that one.
		Ownership of `session_id`, when given, is checked here."""
		if session_id is not None:
			self._require_own_session(session_id)
		return self.metric_service.get_benchmark_metrics(session_id, project_name=project_name)

	def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
		"""Sets (expected_state given) or clears (None) the expert-
		annotated expected state for message_id's evaluation — ownership
		is checked here, the rest is TrackingService's job."""
		self._require_own_message(message_id)
		return self._tracking_service.set_message_expected_state(message_id, expected_state)

	def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
		"""Sets or clears the expert-annotated expected signal values for
		message_id's evaluation — same ownership-then-delegate split as
		set_message_expected_state above."""
		self._require_own_message(message_id)
		return self._tracking_service.set_message_expected_signals(message_id, expected_values)

	def set_message_comment(self, message_id: int, comment: str | None) -> dict | None:
		"""Sets or clears message_id's expert-left free-text comment.
		Unlike set_message_expected_state, every message is a legitimate
		target, so there's no 409 here, only the usual 404 for an unowned message_id."""
		self._require_own_message(message_id)
		return self._tracking_service.set_message_comment(message_id, comment)

	def set_message_reaction(self, message_id: int, reaction: str | None) -> dict | None:
		"""Sets or clears the reaction message_id received from the other
		party — the user's own choice on a bot message. Every message is a
		legitimate target, same as set_message_comment above."""
		self._require_own_message(message_id)
		return self._db.set_message_reaction(message_id, reaction)

	def clear_session_annotations(self, session_id: int) -> None:
		"""Clears every expert annotation (expected_state and
		expected_values alike) across session_id's Tracking rows — the
		"Label sessions" view's "Unlabel all" action."""
		self._require_own_session(session_id)
		self._tracking_service.clear_session_annotations(session_id)

	def get_latest_signals(self) -> list[SignalPayload]:
		return self._tracking_service.get_latest_signals()

	def is_auto_tracking_enabled(self, session_id: int) -> bool:
		self._require_own_session(session_id)
		return self._tracking_service.is_auto_tracking_enabled(session_id)

	def set_auto_tracking_enabled(self, session_id: int, enabled: bool) -> None:
		self._require_own_session(session_id)
		self._tracking_service.set_auto_tracking_enabled(session_id, enabled)

	def clear_auto_tracking_overrides(self) -> None:
		self._tracking_service.clear_auto_tracking_overrides()

	def global_exclusive_access(self):
		return self._global_lock

	@asynccontextmanager
	async def acquire_read(self, project_name: str):
		lock = self._project_locks.get(project_name)
		await lock.acquire_read()
		try:
			yield
		finally:
			await lock.release_read()

	@asynccontextmanager
	async def acquire_write(self, project_name: str):
		lock = self._project_locks.get(project_name)
		await lock.acquire_write()
		try:
			yield
		finally:
			await lock.release_write()

	@asynccontextmanager
	async def _session_scope(self, project_name: str, session_id: int):
		async with self.acquire_read(project_name):
			async with self._session_locks.get(str(session_id)):
				yield

	def _project_name_for_session(self, session_id: int) -> str:
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		return session["project_name"]

	async def open_if_needed(self, session_id: int) -> dict | None:
		# An imported session is a fixed transcript, never live — its NULL
		# message timestamps would make has_messages_since wrongly report
		# "no messages" and crash trying to generate an opening one.
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		if session["type"] == "imported":
			return None

		project_name = session["project_name"]
		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)

		init_message = None
		if self._db.get_current_state(project_name) is None:
			action = automaton.init_action
			# Deliberately never linked to a message: this transition
			# fires before any message of this bootstrap exists, so
			# there's no real "causing" message to attach it to.
			self._db.save_transition(
				"", action.name, state.key, session_id, transition_log_level=state.transition_log_level
			)
			if action.action_prompt:
				init_message = await self._generate_action_prompt_message(action, session_id)

		await self._generate_opening_message_if_needed(session_id, automaton, state)

		return init_message

	def _should_generate_opening_message(self, session_id: int, state: State) -> bool:
		content_since = self._db.history_cutoff_for_session(session_id, state.history_cutoff)
		chat_blocked = state.final or not state.chat
		gate_since = self._db.get_last_transition_timestamp_for_session(session_id) if chat_blocked else content_since
		return not self._db.has_messages_since(session_id, gate_since)

	async def _generate_opening_message_if_needed(
		self, session_id: int, automaton: Automaton, state: State
	) -> dict | None:
		if not self._should_generate_opening_message(session_id, state):
			return None

		return await self._generate_opening_message_body(session_id)

	async def _generate_opening_message_body(self, session_id: int) -> dict:
		return await self.process_turn(session_id)

	async def _generate_action_prompt_message(self, action: Action, session_id: int, *, locked: bool = True) -> dict:
		logger.warning("Executing action_prompt for action '%s'.", action.name)
		if locked:
			return await self.process_turn(session_id, None, extra_prompt=action.action_prompt)
		return await self._process_turn_body(session_id, None, extra_prompt=action.action_prompt)

	async def _messages_for_transition(
		self, action: Action, session_id: int, new_state: State, *, is_self_loop: bool
	) -> list[dict]:
		"""Every real, chat-visible message this transition's follow-up
		turn(s) produced, as flat message rows. A turn whose reply landed
		in a non-chat state has no assistant_message_id, so it's skipped rather than looked up as None."""
		should_open = not is_self_loop and self._should_generate_opening_message(session_id, new_state)

		turn_results = []
		if action.action_prompt:
			turn_results.append(await self._generate_action_prompt_message(action, session_id, locked=False))
		if should_open:
			turn_results.append(await self._process_turn_body(session_id))

		messages = []
		for turn_result in turn_results:
			message_id = turn_result["assistant_message_id"]
			if message_id is None:
				continue
			message = self._db.get_message(message_id)
			if message is not None:
				messages.append(message)
		return messages

	async def apply_manual_action(self, action_name: str, session_id: int) -> dict:
		project_name = self._project_name_for_session(session_id)
		if self._session_locks.get(str(session_id)).locked():
			raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
		async with self._session_scope(project_name, session_id):
			_, source_state = self._project_service.get_automaton_and_state_for_session(session_id)
			# Resolved before applying the action: save_transition (inside
			# project_service.apply_manual_action) now needs a session_id.
			session = self._require_active_session(session_id, project_name, source_state.key)
			state_payload, action, source_state_key = self._project_service.apply_manual_action(
				action_name, session["id"]
			)
			automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
			self._tracking_engine.apply_action_env(
				automaton, action, {}, source_state_key, username=Session().user, project_name=project_name,
			)
			reply = await self._messages_for_transition(
				action, session["id"], state, is_self_loop=(action.target == source_state_key)
			)
			self._session_manager.touch_session(session["id"], state.key)
			return {
				"state": state_payload,
				"reply": reply,
				"on-enter": action.on_enter,
				"ai_model": self.get_ai_models_info(),
				"session_id": session["id"],
			}

	async def process_turn(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		extra_prompt: str | None = None,
	) -> dict:
		project_name = self._project_name_for_session(session_id)
		async with self._session_scope(project_name, session_id):
			return await self._process_turn_body(session_id, text, on_metadata, extra_prompt=extra_prompt)

	async def _process_turn_body(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		extra_prompt: str | None = None,
	) -> dict:
		project_name = self._project_name_for_session(session_id)
		_, state = self._project_service.get_automaton_and_state_for_session(session_id)
		self._require_active_session(session_id, project_name, state.key)
		reply = await self._tracking_service.process(session_id, text, on_metadata, extra_prompt=extra_prompt)
		# touch_session wants the plain state key — reply['state'] is the
		# full StatePayload dict, not a string; passing it whole would
		# silently store its Python repr as end_state.
		self._session_manager.touch_session(reply['session_id'], reply['state']['key'])
		return reply

