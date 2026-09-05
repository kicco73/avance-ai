from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, SignalPayload, State, manual_actions_for
from db import Db, _utc_iso
from ai import AiService
from keyed_lock_registry import KeyedLockRegistry
from project.archive.layout import CACHE_DIR
from project_rw_lock import ProjectRwLock
from session import Session

from tracking.actuators import ActuatorSet, ActuatorSetFactory
from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts
from chat.channels import WHATSAPP_CHAT
from chat.env_for_session import env_for_session
from chat.ephemeral_env_registry import EphemeralEnvRegistry
from chat.errors import ChatServiceError
from chat.session_manager import ChatSessionManager, SessionNotWritable
from chat.session_report_task import SessionReportHydrator, SessionReportScheduler, SessionReportTask
from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from auth.roles import role_satisfies
from job import JobService
from logging_factory import LoggerFactory
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.turn_callbacks import OnMetadata
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

logger = LoggerFactory.get_logger(__name__)

class ChatService(object):
	def __init__(
		self,
		db: Db,
		ai_service: AiService,
		ai_test_service: AiService,
		project_service: ProjectService,
		session_manager: ChatSessionManager,
		tracking_service: TrackingService,
		metric_service: MetricService,
		job_service: JobService,
		actuator_factory: ActuatorSetFactory,
	) -> None:
		self._db = db
		self._ai_service = ai_service
		self._ai_test_service = ai_test_service
		self._project_service = project_service
		self._session_manager = session_manager
		self._tracking_service = tracking_service
		self.metric_service = metric_service
		self._actuator_factory = actuator_factory
		# Shares the platform JobService (see main.py's own wiring) —
		# never its own private queue.
		session_report_hydrator = SessionReportHydrator(db, ai_service)
		job_service.register_task_type(SessionReportTask.TYPE, session_report_hydrator.hydrate)
		session_manager.set_session_report_scheduler(SessionReportScheduler(job_service, session_report_hydrator))
		self._session_facts = SessionFacts(db, project_service)
		self._user_facts = UserFacts(db)
		self._automaton_namespace = AutomatonNamespace(db, project_service)

		self._project_locks = KeyedLockRegistry(ProjectRwLock)
		self._session_locks = KeyedLockRegistry(asyncio.Lock)
		self._session_lifecycle_locks = KeyedLockRegistry(asyncio.Lock)
		self._global_lock = asyncio.Lock()

	def _ai_service_for_session(self, session_id: int) -> AiService:
		session = self._db.get_chat_session(session_id)
		return self._ai_test_service if session is not None and session["type"] in ("test", "preview") else self._ai_service

	def _require_session(self, session_id: int) -> dict:
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		return session

	def _env_for_session(self, session_id: int) -> Env:
		"""The Env `session_id` actually owns: ephemeral (in-memory, never
		persisted) for a test/preview session, or PersistedEnv pinned to
		its own project and user for a live/imported one — never a
		request-user-wide env, which is what let a session of another
		project (the Sessions panel, a supervisor reading someone else's
		session, WhatsApp) read/write a completely different project's
		Tracking rows. See chat.env_for_session's own docstring for why a
		test/preview session's env must never reach the database at all."""
		return env_for_session(self._db, self._require_session(session_id))

	def _tracking_engine_for_session(self, session_id: int) -> tuple[TrackingEngine, "ActuatorSet"]:
		"""Same per-session pinning TrackingService.process already does
		with its own FixedProjectContext: env and session facts are the
		session's, not the request user's active project's."""
		session = self._require_session(session_id)
		fixed_context = FixedProjectContext(project_id=session["project_id"])
		env = env_for_session(self._db, session)
		session_facts = SessionFacts(self._db, fixed_context)
		actuator_set = self._actuator_factory.for_session(session_id)
		scope_builder = EvaluationScopeBuilder(
			env, self.metric_service, session_facts, self._user_facts,
			self._db, self._automaton_namespace, actuator_set, ai_service=self._ai_service_for_session(session_id),
		)
		return TrackingEngine(DbTrackingSink(self._db), env, scope_builder), actuator_set

	def _schedule_on_enter(self, automaton: Automaton, action: Action, session_id: int | None, project_id: str) -> None:
		"""`action.on_enter` (celebrate/notify/send_mail/prompt-style
		actuator.* calls, or nothing) scheduled as an OnEnterTask — what
		it produces reaches the browser over the websocket, never inside
		this response. Used wherever an action's on-enter fires outside a
		real trigger/apply_transition path (new-session bootstrap,
		test-session reset). `session_id=None` (no session yet, e.g. a
		project-wide test reset) always goes through a FakeActuatorSet,
		same as any other actuator-off default — and actuator.prompt()
		itself returns "" there (no AiService wired for this scope)."""
		if not action.on_enter:
			return
		if session_id is not None:
			tracking_engine, _ = self._tracking_engine_for_session(session_id)
		else:
			# Only ever reached from reset_test_sessions's own project-wide
			# reset (no single session to pin to yet) — a fresh, empty Env,
			# never PersistedEnv: this on-enter belongs to the test
			# automaton, and a test session's own env must never touch the
			# database (see chat.env_for_session).
			env = Env()
			scope_builder = EvaluationScopeBuilder(
				env, self.metric_service, self._session_facts, self._user_facts,
				self._db, self._automaton_namespace, self._actuator_factory.fake(project_id=project_id),
			)
			tracking_engine = TrackingEngine(DbTrackingSink(self._db), env, scope_builder)
		tracking_engine.schedule_on_enter(automaton, action, action.target, session_id=session_id)

	@property
	def _active_project_id(self) -> str:
		return self._project_service.get_active_project_id()

	@property
	def _username(self) -> str:
		return Session().user

	def _owns_session(self, session_username: str) -> bool:
		if session_username == self._username:
			return True
		return role_satisfies(Session().role, 'supervisor')

	def get_message_audio_text(self, message_id: int) -> str | None:
		return self._db.get_message_audio_text(message_id)

	def get_ai_models_info(self) -> dict:
		return self._ai_service.get_models_info()

	def select_ai_model(self, index: int | None) -> None:
		self._ai_service.select_model(index)

	def get_test_ai_models_info(self) -> dict:
		return self._ai_test_service.get_models_info()

	def select_test_ai_model(self, index: int | None) -> None:
		self._ai_test_service.select_model(index)

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
			"project_id": session["project_id"],
			"type": session["type"],
			"title": session["title"],
			"datetime_start": _utc_iso(session["datetime_start"]),
			"datetime_end": _utc_iso(session["datetime_end"]),
			"start_state": session["start_state"],
			"end_state": session["end_state"],
			"channel": session["channel"],
			"closed_at": _utc_iso(session["closed_at"]),
			"close_reason": session["close_reason"],
			# An imported session is always expired — never "open".
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
			"ai_summary": session["ai_summary"],
		}

	def _require_active_session(self, session_id: int | None, project_id: str, current_state: str) -> dict:
		"""A chat turn's session must already be the active one for this
		project — rejected just as firmly if merely open-but-superseded
		as if outright closed. ValueError becomes a 409 the frontend can act on."""
		try:
			return self._session_manager.require_active_session(
				self._username, project_id, session_id, current_state
			)
		except SessionNotWritable as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT, code=exc.code) from exc

	def get_legal_terms_status(self, project_id: str) -> dict:
		return self._project_service.get_legal_terms_status(self._username, project_id)

	def accept_legal_terms(self, project_id: str) -> None:
		self._project_service.accept_legal_terms(self._username, project_id)

	def _legal_terms_pending_response(self, project_id: str) -> dict | None:
		# Only a session about to be created is pinned to
		# get_published_revision — an already-open one keeps running
		# against whatever revision it was created against, so it's
		# never blocked by terms published since.
		if self._project_service.legal_terms_pending(self._username, project_id):
			return {"legal_terms_pending": True, "project_id": project_id}
		return None

	def _session_response(self, session: dict, *, active: bool) -> dict:
		automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
		state_payload = self._with_manual_actions(session["id"], automaton.get_state_payload(state))
		return {**self._session_payload(session, active=active), "state": state_payload}

	async def _get_current_session_if_any_or_create_new_of_type(
		self, strategy: SessionTypeStrategy, project_id: str, session_id: int | None
	) -> dict:
		async with self._session_lifecycle_scope(self._username, project_id):
			try:
				if strategy.type_name == 'live' and self._session_manager.get_active_session(self._username, project_id) is None:
					pending = self._legal_terms_pending_response(project_id)
					if pending is not None:
						return pending
				_, state = self._project_service.get_automaton_and_state(
					project_id, type=strategy.type_name, username=self._username
				)
				session = self._session_manager.get_current_session_if_any_or_create_new(
					strategy, self._project_service, self._username, project_id, session_id, state.key
				)
			except ValueError as exc:
				raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return self._session_response(session, active=session["channel"] == Session().channel)

	async def get_current_session_if_any_or_create_new(self, session_id: int | None) -> dict:
		"""Bootstrap for a client with no (or a possibly-stale) session_id:
		resolves or creates the one session currently writable for the
		active project. A paused project skips bootstrapping entirely."""
		project_id = self._active_project_id
		is_paused, paused_reason = self._project_service.get_project_availability(project_id)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('live'), project_id, session_id)

	async def get_current_draft_session_if_any_or_create_new(self, session_id: int | None, project_id: str) -> dict:
		"""Like get_current_session_if_any_or_create_new, but for
		`project_id`'s own *draft* — the embedded "Test" chat is the one
		place a session is allowed to exist against an unpublished
		revision. `project_id` comes from the URL, never the
		active-project pointer."""
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('test'), project_id, session_id)

	async def get_current_preview_session_if_any_or_create_new(self, session_id: int | None, project_id: str) -> dict:
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('preview'), project_id, session_id)

	async def acquire_exclusive_session(self) -> dict:
		"""Like get_current_session_if_any_or_create_new(None), but with
		real intent to write through the current channel right now: an
		open session on another channel is closed and superseded instead
		of returned as-is."""
		project_id = self._active_project_id
		is_paused, paused_reason = self._project_service.get_project_availability(project_id)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		async with self._session_lifecycle_scope(self._username, project_id):
			if self._session_manager.get_active_session(self._username, project_id) is None:
				pending = self._legal_terms_pending_response(project_id)
				if pending is not None:
					return pending
			_, state = self._project_service.get_automaton_and_state(project_id, type='live', username=self._username)
			try:
				session = self._session_manager.acquire_exclusive_session(
					get_session_type_strategy('live'), self._project_service, self._username, project_id, state.key
				)
			except ValueError as exc:
				raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return self._session_response(session, active=True)

	async def record_whatsapp_send(self, username: str, project_id: str, content: str) -> None:
		async with self._session_lifecycle_scope(username, project_id):
			session = self._session_manager.get_active_session(username, project_id)
			if session is None:
				Session().channel = WHATSAPP_CHAT
				session = self._session_manager.create_session(
					get_session_type_strategy('live'), self._project_service, username, project_id,
				)
			self._db.save_message('assistant', content, session["id"])

	async def _create_session_of_type(self, strategy: SessionTypeStrategy, project_id: str) -> dict:
		async with self._session_lifecycle_scope(self._username, project_id):
			try:
				if strategy.type_name == 'live':
					if self._project_service.legal_terms_pending(self._username, project_id):
						return {"legal_terms_pending": True, "project_id": project_id}
					active = self._session_manager.get_active_session(self._username, project_id)
					if active is not None:
						reason = "force-new-session" if active["channel"] == Session().channel else "channel-switch"
						self._session_manager.close_session(active, reason)
				session = self._session_manager.create_session(
					strategy, self._project_service, self._username, project_id
				)
			except ValueError as exc:
				raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		automaton = self._project_service.get_automaton_for_session(session["id"])
		payload = self._session_payload(session, active=True)
		if strategy.on_enter_for_new_session(automaton) is not None:
			self._schedule_on_enter(automaton, automaton.init_action, session["id"], project_id)
		return payload

	async def create_session(self) -> dict:
		return await self._create_session_of_type(get_session_type_strategy('live'), self._active_project_id)

	async def create_draft_session(self, project_id: str) -> dict:
		"""Like create_session, but against `project_id`'s own current
		*draft* revision, always starting fresh from the automaton's own
		initial state — the embedded "Test" chat is the only caller.
		`project_id` comes from the URL, never the active-project pointer."""
		return await self._create_session_of_type(get_session_type_strategy('test'), project_id)

	async def create_preview_session(self, project_id: str) -> dict:
		deleted_ids = self._db.delete_sessions_by_username_and_type(self._username, 'preview')
		for deleted_id in deleted_ids:
			EphemeralEnvRegistry().discard(deleted_id)
		return await self._create_session_of_type(get_session_type_strategy('preview'), project_id)

	def reset_test_sessions(self, project_id: str) -> dict:
		reset_session_ids = [
			session["id"] for session in self._db.list_chat_sessions(self._username, project_id, type='test')
		]
		self._project_service.reset_test_sessions(project_id)
		for reset_id in reset_session_ids:
			EphemeralEnvRegistry().discard(reset_id)
		automaton, state = self._project_service.get_automaton_and_state(project_id, type='test')
		self._schedule_on_enter(automaton, automaton.init_action, None, project_id)
		return automaton.get_state_payload(state)

	def _list_sessions_by_type(self, project_id: str, type: str | tuple[str, ...], active_type: str) -> list[dict]:
		sessions = self._db.list_chat_sessions(None, project_id, type=type)
		sessions = [s for s in sessions if self._owns_session(s['username'])]
		active = self._session_manager.get_active_session(self._username, project_id, type=active_type)
		return [
			self._session_payload(s, active=get_session_type_strategy(s["type"]).is_valid_write_target(s, active))
			for s in sessions
		]

	def list_sessions(self, project_id: str, include_imported: bool = False) -> list[dict]:
		"""Every real (live, and optionally imported) session for
		`project_id`, most recently started first — for the "Sessions"
		panel. Never a 'test' session (see list_test_sessions instead)."""
		type = ('live', 'imported') if include_imported else 'live'
		return self._list_sessions_by_type(project_id, type, active_type='live')

	def list_test_sessions(self, project_id: str) -> list[dict]:
		"""Like list_sessions, but `project_id`'s own 'test' sessions —
		live/imported never appear here, symmetric to how a test session
		never appears in list_sessions."""
		return self._list_sessions_by_type(project_id, 'test', active_type='test')

	def _require_own_session(self, session_id: int) -> None:
		"""Raises (404) unless `session_id` still exists and belongs to
		the current user — sessions can be deleted independently, so a
		caller about to write to one can't just assume it's still there."""
		session = self._db.get_chat_session(session_id)
		if session is None or not self._owns_session(session["username"]):
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)

	def delete_session(self, session_id: int) -> None:
		"""Deletes `session_id` and everything scoped to it (see
		db.delete_chat_session) — only the current user's own sessions,
		never someone else's by guessing an id."""
		self._require_own_session(session_id)
		project_id = self._project_id_for_session(session_id)
		self._db.delete_chat_session(session_id)
		EphemeralEnvRegistry().discard(session_id)
		# A source's own per-session read cache (tracking.sources.
		# avance_archive) — same cleanup ChatSessionManager.close_session
		# does, needed here too since a session can be deleted outright
		# without ever going through close_session first.
		self._db.delete_archives_with_prefix(project_id, f"{CACHE_DIR}/sessions/{session_id}/")

	def clear_session_env(self, session_id: int) -> None:
		self._require_own_session(session_id)
		self._env_for_session(session_id).clear()

	async def close_session(self, session_id: int) -> dict:
		"""Explicit "close session" action (the live chat's own
		applications menu) — ends session_id outright, unlike
		create_session's own close-and-replace. A no-op (see
		ChatSessionManager.close_session) if it's already closed."""
		self._require_own_session(session_id)
		project_id = self._project_id_for_session(session_id)
		async with self._session_lifecycle_scope(self._username, project_id):
			session = self._db.get_chat_session(session_id)
			assert session is not None
			self._session_manager.close_session(session, "manual-user")
		EphemeralEnvRegistry().discard(session_id)
		return self._reloaded_session_payload(session_id)

	def _reloaded_session_payload(self, session_id: int) -> dict:
		"""Common tail for every "write one field, then hand back a fresh
		payload" mutation below."""
		session = self._db.get_chat_session(session_id)
		assert session is not None
		strategy = get_session_type_strategy(session["type"])
		active_session = self._session_manager.get_active_session(self._username, session["project_id"], type=session["type"])
		return self._session_payload(session, active=strategy.is_valid_write_target(session, active_session))

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
		project_id = self._project_id_for_session(session_id)
		async with self._session_scope(project_id, session_id):
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
		return self._with_manual_actions(session_id, automaton.get_state_payload(state))

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
			if session is not None and self._owns_session(session["username"]):
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
		self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None,
	) -> list[dict]:
		"""metrics.metrics_framework's core metrics for `project_id` —
		the full current history, or (`message_id` given) restricted to
		what existed at or before that message's own timestamp. `full`:
		every core metric, not just the "one_session" subset — see
		MetricService.calculate_all's own `include_all_scopes`. `username`
		omitted means the caller's own sessions — see its own `username`."""
		until = self._until_from_message(message_id)
		return self.metric_service.calculate_all(
			until=until, project_id=project_id, include_all_scopes=full, username=username,
		)

	def _session_start_marker(self, session: dict) -> str | None:
		if session['datetime_start'] is not None:
			return _utc_iso(session['datetime_start'])
		messages = self._db.get_messages(session['id'])
		return messages[0]['timestamp'] if messages else None

	def get_metrics_history(self, project_id: str, username: str) -> dict:
		# Sorted by the same value each point is actually plotted at
		# (`until` below) — sorting by datetime_start instead let an
		# overlapping session (e.g. a longer-running one whose datetime_end
		# lands after a later-started, shorter one's) put a later point
		# before an earlier one, which Chart.js then draws as the line
		# jumping backward in time instead of connecting points left to right.
		# An imported transcript can carry no timestamps at all
		# (datetime_start/datetime_end both NULL — see
		# SessionImportManager.import_transcript): there's no instant to
		# plot it at, so it contributes no point rather than making the
		# sort below compare None with None (a 500 for every user of a
		# project with such sessions).
		sessions = sorted(
			(
				session for session in self._db.list_chat_sessions(username, project_id, type=None)
				if (session['datetime_end'] or session['datetime_start']) is not None
			),
			key=lambda session: session['datetime_end'] or session['datetime_start'],
		)
		history = []
		session_starts = []
		for session in sessions:
			until = session['datetime_end'] or session['datetime_start']
			results = self.metric_service.calculate_all(
				until=until, project_id=project_id, include_all_scopes=True, username=username,
			)
			values = {result['name']: result['value'] for result in results if result['value'] is not None}
			if values:
				history.append({'timestamp': _utc_iso(until), 'values': values})
			marker = self._session_start_marker(session)
			if marker is not None:
				session_starts.append({'timestamp': marker, 'title': session['title'], 'end_timestamp': _utc_iso(until)})
		return {'metrics': history, 'session_starts': session_starts}

	def get_latest_signal_values(self, project_id: str, username: str) -> dict:
		sessions = self._db.list_chat_sessions(username, project_id, type='live')
		if not sessions:
			return {'last_session': None, 'session_id': None, 'values': None}
		last_session = sessions[0]
		last_session_payload = {
			'id': last_session['id'], 'start_state': last_session['start_state'], 'end_state': last_session['end_state'],
		}
		for session in sessions:
			for row in reversed(self._db.get_signals(session['id'])):
				if row['values'] is not None:
					return {'last_session': last_session_payload, 'session_id': session['id'], 'values': row['values']}
		return {'last_session': last_session_payload, 'session_id': last_session['id'], 'values': None}

	def get_timeline(self, project_id: str, username: str) -> dict:
		"""Real transitions from this user's own sessions, plus — regardless
		of which user's session it belongs to — the one transition that
		bootstrapped the project's automaton into its initial state: the
		automaton's current state is shared project-wide, not per-user, so
		that single event is what "the initial state" means for every
		user's timeline, not whichever state a later session happened to
		start in (see open_if_needed)."""
		data = self._db.get_timeline(project_id, username)
		transitions = list(data['transitions'])
		init_transition = self._db.get_project_init_transition(project_id)
		if init_transition is not None and init_transition not in transitions:
			transitions.append(init_transition)
		transitions.sort(key=lambda transition: transition['timestamp'])
		return {'signals': data['signals'], 'transitions': transitions}

	def get_env(self, session_id: int, message_id: int | None = None) -> dict:
		"""{"stored": ..., "action_set": ...} for `session_id`'s own env,
		reported separately so the Inspector Env tab knows which section
		each value belongs in and which are editable. Current, or as of
		`message_id` if given — for a test/preview session this is always
		current: an in-memory Env keeps no history to look back through
		(see chat.env_for_session and tracking.env.Env.stored's own
		docstring), so `message_id` is accepted but has no effect there."""
		self._require_own_session(session_id)
		until = self._until_from_message(message_id)
		env = self._env_for_session(session_id)
		return {
			"stored": env.stored(until),
			"action_set": env.action_set(until),
		}

	def set_env_value(self, session_id: int, key: str, value: str) -> dict:
		"""Edits one stored env key on `session_id`'s own env — always
		current, no "editing history"."""
		self._require_own_session(session_id)
		self._env_for_session(session_id).set_value(key, value)
		return self.get_env(session_id)

	def delete_env_key(self, session_id: int, key: str) -> dict:
		"""Removes one stored env key outright (see chat.env.Env.
		delete_key) — always current. Returns the same shape as get_env."""
		self._require_own_session(session_id)
		self._env_for_session(session_id).delete_key(key)
		return self.get_env(session_id)

	def clear_env(self, session_id: int) -> dict:
		"""Wipes every stored and action-set env key at once (see
		chat.env.Env.clear). Always current. Returns the same shape as get_env."""
		self._require_own_session(session_id)
		self._env_for_session(session_id).clear()
		return self.get_env(session_id)

	def get_benchmark_metrics(self, project_id: str, session_id: int | None = None) -> list[dict]:
		"""Expert-annotation-vs-actual benchmark metrics for `project_id`
		— every annotated session, or (session_id given) just that one.
		Ownership of `session_id`, when given, is checked here."""
		if session_id is not None:
			self._require_own_session(session_id)
		return self.metric_service.get_benchmark_metrics(session_id, project_id=project_id)

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

	def get_input_token_budget_per_turn(self) -> int | None:
		return self._tracking_service.get_input_token_budget_per_turn()

	def get_total_token_budget_per_session(self) -> int | None:
		return self._tracking_service.get_total_token_budget_per_session()

	def is_auto_tracking_enabled(self, session_id: int) -> bool:
		self._require_own_session(session_id)
		return self._tracking_service.is_auto_tracking_enabled(session_id)

	def set_auto_tracking_enabled(self, session_id: int, enabled: bool) -> None:
		self._require_own_session(session_id)
		self._tracking_service.set_auto_tracking_enabled(session_id, enabled)

	def _with_manual_actions(self, session_id: int, state_payload: dict) -> dict:
		auto_tracking_enabled = self.is_auto_tracking_enabled(session_id)
		return {**state_payload, "manual_actions": manual_actions_for(state_payload["actions"], auto_tracking_enabled)}

	def is_actuators_enabled(self, session_id: int) -> bool:
		self._require_own_session(session_id)
		return self._actuator_factory.is_enabled_for_test_session(session_id)

	def set_actuators_enabled(self, session_id: int, enabled: bool) -> None:
		self._require_own_session(session_id)
		self._actuator_factory.set_enabled_for_test_session(session_id, enabled)

	def clear_auto_tracking_overrides(self) -> None:
		self._tracking_service.clear_auto_tracking_overrides()

	def global_exclusive_access(self):
		return self._global_lock

	@asynccontextmanager
	async def acquire_read(self, project_id: str):
		lock = self._project_locks.get(project_id)
		await lock.acquire_read()
		try:
			yield
		finally:
			await lock.release_read()

	@asynccontextmanager
	async def acquire_write(self, project_id: str):
		lock = self._project_locks.get(project_id)
		await lock.acquire_write()
		try:
			yield
		finally:
			await lock.release_write()

	@asynccontextmanager
	async def _session_scope(self, project_id: str, session_id: int):
		async with self.acquire_read(project_id):
			async with self._session_locks.get(str(session_id)):
				yield

	@asynccontextmanager
	async def _session_lifecycle_scope(self, username: str, project_id: str):
		async with self._session_lifecycle_locks.get(f"{username}/{project_id}"):
			yield

	def _project_id_for_session(self, session_id: int) -> str:
		return self._require_session(session_id)["project_id"]

	def _apply_declared_env_defaults(self, automaton: Automaton, project_id: str, session_id: int) -> None:
		"""See open_if_needed's own call site. `automaton.init_action.env`
		carries every project-level `env:` declaration's default
		(AutomatonBuilder folds them in, in declaration order), evaluated
		through the same eval_action_env path a real action's own `env:`
		uses via TrackingEngine.apply_action_env — but one key at a time,
		not as a single batch: AutomatonBuilder._validate_env_key_default_
		order guarantees a later key's default may only reference an
		earlier one, so each key's own evaluation needs that earlier
		key's value already persisted (a single shared apply_action_env
		call evaluates every key against one static scope snapshot taken
		before any of them run, which a same-batch reference could never
		see). Only whichever keys don't already have a stored or
		action-set value are (re-)evaluated."""
		action = automaton.init_action
		if not action.env:
			return
		# The session's own env (see _env_for_session) — the same one
		# tracking_engine below writes through, so "already has a value"
		# is answered by the project the defaults belong to.
		env = self._env_for_session(session_id)
		current = {**env.stored(), **env.action_set()}
		missing = {key: expression for key, expression in action.env.items() if key not in current}
		if not missing:
			return
		tracking_engine, _ = self._tracking_engine_for_session(session_id)
		for key, expression in missing.items():
			tracking_engine.apply_action_env(
				automaton, replace(action, env={key: expression}, on_enter=None), {}, "",
				username=self._username, project_id=project_id, session_id=session_id,
			)

	def _cleanup_orphan_action_env_keys(
		self, automaton: Automaton, project_id: str, session_id: int, session_type: str
	) -> None:
		"""A live session's own action_set() keys the project's *current*
		revision no longer declares under `env:` get dropped here, once
		per session opened against that revision — self-limiting exactly
		like _apply_declared_env_defaults above: once nothing's left to
		drop, every later open is a no-op read, never a write. Live only —
		a test/preview session's own env is ephemeral and starts empty
		every time (see chat.env_for_session), so there is nothing for it
		to have accumulated. This is what actually removes a key like a
		leftover `flight_record` from a since-edited project (see the
		one-time migration for rows already stuck in that state before
		this existed)."""
		if session_type != "live":
			return
		env = self._env_for_session(session_id)
		declared = {env_key.name for env_key in automaton.env_keys}
		orphans = set(env.action_set()) - declared
		if not orphans:
			return
		env.drop_action_set_keys(orphans)
		logger.warning(
			"Session %s (project '%s'): dropped orphaned action_env key(s) %s — no longer declared by the "
			"current revision's own 'env' section.", session_id, project_id, sorted(orphans),
		)

	async def _ensure_project_bootstrap(
		self, session_id: int
	) -> tuple[Automaton, State] | tuple[None, None]:
		# An imported session is a fixed transcript, never live — its NULL
		# message timestamps would make has_messages_since wrongly report
		# "no messages" and crash trying to generate an opening one.
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		if session["type"] == "imported":
			return None, None

		project_id = session["project_id"]
		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)

		# Every declared env key's default (folded into init_action's own
		# `env:` by AutomatonBuilder) — checked on every open, not just
		# the project's first-ever bootstrap below, so a key declared
		# after this project already had sessions still gets picked up
		# the next time one is opened. Self-limiting: only ever fills in
		# a key nothing has set yet, so it never clobbers a value the
		# model/an action/the user set afterwards, and it's a no-op
		# (no DB write) once every declared key already has one.
		self._apply_declared_env_defaults(automaton, project_id, session_id)
		self._cleanup_orphan_action_env_keys(automaton, project_id, session_id, session["type"])

		if self._db.get_current_state(project_id) is None:
			action = automaton.init_action
			# Deliberately never linked to a message: this transition
			# fires before any message of this bootstrap exists, so
			# there's no real "causing" message to attach it to.
			self._db.save_transition(
				"", action.name, state.key, session_id, transition_log_level=state.transition_log_level,
				origin='init-action',
			)

		return automaton, state

	async def open_if_needed(self, session_id: int) -> dict | None:
		automaton, state = await self._ensure_project_bootstrap(session_id)
		if automaton is None:
			return None
		await self._generate_opening_message_if_needed(session_id, automaton, state)
		return None

	async def prepare_user_initiated_turn(self, session_id: int) -> None:
		"""WhatsApp's own turns (invite welcome excluded): the user's text
		is what starts or continues the conversation, so no AI-initiated
		opening message runs ahead of it — only the project bootstrap
		above, plus (when the current state can't take a real turn at
		all) the wrap-up message that's otherwise the only thing this
		session would ever say."""
		automaton, state = await self._ensure_project_bootstrap(session_id)
		if automaton is None:
			return
		if state.final or not state.chat:
			await self._generate_opening_message_if_needed(session_id, automaton, state)

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

	async def _messages_for_transition(
		self, session_id: int, new_state: State, *, is_self_loop: bool
	) -> list[dict]:
		"""The new state's own opening message, if this transition
		generated one, as a flat message row (list-wrapped for
		apply_manual_action's own "reply" field). A turn whose reply
		landed in a non-chat state has no assistant_message_id, so
		nothing is returned rather than looked up as None."""
		should_open = not is_self_loop and self._should_generate_opening_message(session_id, new_state)
		if not should_open:
			return []
		turn_result = await self._process_turn_body(session_id)
		message_id = turn_result["assistant_message_id"]
		if message_id is None:
			return []
		message = self._db.get_message(message_id)
		return [message] if message is not None else []

	async def apply_manual_action(self, action_name: str, session_id: int) -> dict:
		project_id = self._project_id_for_session(session_id)
		if self._session_locks.get(str(session_id)).locked():
			raise ChatServiceError(
				"A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT, code="turn_in_progress",
			)
		async with self._session_scope(project_id, session_id):
			_, source_state = self._project_service.get_automaton_and_state_for_session(session_id)
			# Resolved before applying the action: save_transition (inside
			# project_service.apply_manual_action) now needs a session_id.
			session = self._require_active_session(session_id, project_id, source_state.key)
			state_payload, action, source_state_key = self._project_service.apply_manual_action(
				action_name, session["id"]
			)
			automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
			tracking_engine, _ = self._tracking_engine_for_session(session["id"])
			tracking_engine.apply_action_env(
				automaton, action, {}, source_state_key, username=Session().user, project_id=project_id,
				session_id=session["id"],
			)
			reply = await self._messages_for_transition(
				session["id"], state, is_self_loop=(action.target == source_state_key)
			)
			self._session_manager.touch_session(session["id"], state.key)
			return {
				"state": self._with_manual_actions(session["id"], state_payload),
				"reply": reply,
				"ai_model": self.get_ai_models_info(),
				"session_id": session["id"],
			}

	async def process_turn(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
	) -> dict:
		project_id = self._project_id_for_session(session_id)
		async with self._session_scope(project_id, session_id):
			return await self._process_turn_body(session_id, text, on_metadata)

	async def _process_turn_body(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
	) -> dict:
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		project_id = session["project_id"]
		ai_service = self._ai_test_service if session["type"] == "test" else self._ai_service
		_, state = self._project_service.get_automaton_and_state_for_session(session_id)
		self._require_active_session(session_id, project_id, state.key)
		reply = await self._tracking_service._process(session_id, text, ai_service, on_metadata)
		# touch_session wants the plain state key — reply['state'] is the
		# full StatePayload dict, not a string; passing it whole would
		# silently store its Python repr as end_state.
		self._session_manager.touch_session(reply['session_id'], reply['state']['key'])
		reply['state'] = self._with_manual_actions(session_id, reply['state'])
		return reply

