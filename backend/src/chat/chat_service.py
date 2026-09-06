from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, SignalPayload, State, manual_actions_for
from automaton.build_error import AutomatonBuildError
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
from chat.sessions.session_insights import SessionInsights
from chat.sessions.session_ownership import SessionOwnership
from chat.session_report_task import SessionReportHydrator, SessionReportScheduler, SessionReportTask
from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
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
		session_report_hydrator = SessionReportHydrator(db, ai_service)
		job_service.register_task_type(SessionReportTask.TYPE, session_report_hydrator.hydrate)
		session_manager.set_session_report_scheduler(SessionReportScheduler(job_service, session_report_hydrator))
		self._ownership = SessionOwnership(db)
		self._insights = SessionInsights(db, metric_service, tracking_service, self._ownership)
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

	def _env_for_session(self, session_id: int) -> Env:
		return env_for_session(self._db, self._ownership.require_session(session_id))

	def _tracking_engine_for_session(self, session_id: int) -> tuple[TrackingEngine, "ActuatorSet"]:
		session = self._ownership.require_session(session_id)
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
		if not action.on_enter:
			return
		if session_id is not None:
			tracking_engine, _ = self._tracking_engine_for_session(session_id)
		else:
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
		return [{"role": m["role"], "content": m["content"]} for m in history]

	def _session_payload(self, session: dict, *, active: bool) -> dict:
		return {
			"id": session["id"],
			"username": session["username"],
			"project_id": session["project_id"],
			"project_revision": session["project_revision"],
			"type": session["type"],
			"title": session["title"],
			"datetime_start": _utc_iso(session["datetime_start"]),
			"datetime_end": _utc_iso(session["datetime_end"]),
			"start_state": session["start_state"],
			"end_state": session["end_state"],
			"channel": session["channel"],
			"closed_at": _utc_iso(session["closed_at"]),
			"close_reason": session["close_reason"],
			"open": self._session_manager.is_open(session),
			"active": active,
			"has_annotations": session["labeled"],
			"comment": session["comment"],
			"ai_summary": session["ai_summary"],
		}

	def _session_revision_unsupported(self, session: dict) -> bool:
		if session["type"] == "test":
			return False
		try:
			self._project_service.get_automaton(session["project_id"], session["project_revision"])
		except (AutomatonBuildError, FileNotFoundError, ValueError):
			return True
		return False

	def _ensure_project_available(self, project_id: str) -> None:
		is_paused, paused_reason = self._project_service.get_project_availability(project_id)
		if is_paused:
			raise ChatServiceError(
				paused_reason or "This project is currently paused.",
				status_code=HTTPStatus.CONFLICT, code="project_unavailable",
			)

	def _get_automaton_and_state_or_raise_unsupported(self, session_id: int, session: dict) -> tuple[Automaton, State]:
		try:
			return self._project_service.get_automaton_and_state_for_session(session_id)
		except (AutomatonBuildError, FileNotFoundError, ValueError) as exc:
			if session["type"] == "test":
				raise
			raise ChatServiceError(
				f"This session is pinned to revision {session['project_revision']}, which this version of "
				"Avance can no longer run.",
				status_code=HTTPStatus.CONFLICT, code="session_revision_unsupported",
			) from exc

	def _require_active_session(self, session_id: int | None, project_id: str, current_state: str) -> dict:
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
		project_id = self._active_project_id
		is_paused, paused_reason = self._project_service.get_project_availability(project_id)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('live'), project_id, session_id)

	async def get_current_draft_session_if_any_or_create_new(self, session_id: int | None, project_id: str) -> dict:
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('test'), project_id, session_id)

	async def get_current_preview_session_if_any_or_create_new(self, session_id: int | None, project_id: str) -> dict:
		return await self._get_current_session_if_any_or_create_new_of_type(get_session_type_strategy('preview'), project_id, session_id)

	async def acquire_exclusive_session(self) -> dict:
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
		sessions = [s for s in sessions if self._ownership.owns_session(s['username'])]
		active = self._session_manager.get_active_session(self._username, project_id, type=active_type)
		return [
			{
				**self._session_payload(s, active=get_session_type_strategy(s["type"]).is_valid_write_target(s, active)),
				"unsupported_revision": self._session_revision_unsupported(s),
			}
			for s in sessions
		]

	def list_sessions(self, project_id: str, include_imported: bool = False) -> list[dict]:
		type = ('live', 'imported') if include_imported else 'live'
		return self._list_sessions_by_type(project_id, type, active_type='live')

	def list_test_sessions(self, project_id: str) -> list[dict]:
		return self._list_sessions_by_type(project_id, 'test', active_type='test')

	def delete_session(self, session_id: int) -> None:
		self._ownership.require_own_session(session_id)
		project_id = self._project_id_for_session(session_id)
		self._db.delete_chat_session(session_id)
		EphemeralEnvRegistry().discard(session_id)
		self._db.delete_archives_with_prefix(project_id, f"{CACHE_DIR}/sessions/{session_id}/")

	def clear_session_env(self, session_id: int) -> None:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).clear()

	async def close_session(self, session_id: int) -> dict:
		self._ownership.require_own_session(session_id)
		project_id = self._project_id_for_session(session_id)
		async with self._session_lifecycle_scope(self._username, project_id):
			session = self._db.get_chat_session(session_id)
			assert session is not None
			self._session_manager.close_session(session, "manual-user")
		EphemeralEnvRegistry().discard(session_id)
		return self._reloaded_session_payload(session_id)

	def _reloaded_session_payload(self, session_id: int) -> dict:
		session = self._db.get_chat_session(session_id)
		assert session is not None
		strategy = get_session_type_strategy(session["type"])
		active_session = self._session_manager.get_active_session(self._username, session["project_id"], type=session["type"])
		return self._session_payload(session, active=strategy.is_valid_write_target(session, active_session))

	def set_session_title(self, session_id: int, title: str | None) -> dict:
		self._ownership.require_own_session(session_id)
		stripped = title.strip() if title is not None else None
		self._db.set_session_title(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def set_session_comment(self, session_id: int, comment: str | None) -> dict:
		self._ownership.require_own_session(session_id)
		stripped = comment.strip() if comment is not None else None
		self._db.set_session_comment(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def mark_session_labeled(self, session_id: int, labeled: bool) -> dict:
		self._ownership.require_own_session(session_id)
		self._db.set_session_labeled(session_id, labeled)
		return self._reloaded_session_payload(session_id)

	async def truncate_session(self, session_id: int, timestamp: str) -> None:
		self._ownership.require_own_session(session_id)
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
		self._ownership.require_own_session(session_id)
		session = self._db.get_chat_session(session_id)
		assert session is not None
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(session_id, session)
		return self._with_manual_actions(session_id, automaton.get_state_payload(state))

	async def get_messages(self, session_id: int, last_n: int | None = None) -> list[dict]:
		self._ownership.require_own_session(session_id)
		init_message = await self.open_if_needed(session_id)
		messages = self._db.get_messages(session_id, last_n=last_n)
		if init_message is not None:
			messages.insert(0, init_message)
		tool_calls_by_message = self._db.get_tool_calls_by_message(session_id)
		for message in messages:
			tool_calls = tool_calls_by_message.get(message["id"])
			if tool_calls:
				message["tool_calls"] = tool_calls
		return messages

	def get_env(self, session_id: int, message_id: int | None = None) -> dict:
		self._ownership.require_own_session(session_id)
		until = self._ownership.until_from_message(message_id)
		env = self._env_for_session(session_id)
		automaton = self._project_service.get_automaton_for_session(session_id)
		return {
			"memory": env.memory(until),
			"action_set": env.action_set(until),
			"ai_access": {env_key.name: env_key.ai_access for env_key in automaton.env_keys},
		}

	def set_env_value(self, session_id: int, key: str, value: str) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).set_value(key, value)
		return self.get_env(session_id)

	def delete_env_key(self, session_id: int, key: str) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).delete_key(key)
		return self.get_env(session_id)

	def clear_env(self, session_id: int) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).clear()
		return self.get_env(session_id)

	def get_session_signals(self, session_id: int) -> list[dict]:
		return self._insights.get_session_signals(session_id)

	def get_metrics(
		self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None,
	) -> list[dict]:
		return self._insights.get_metrics(project_id, message_id, full, username)

	def get_metrics_history(self, project_id: str, username: str) -> dict:
		return self._insights.get_metrics_history(project_id, username)

	def get_latest_signal_values(self, project_id: str, username: str) -> dict:
		return self._insights.get_latest_signal_values(project_id, username)

	def get_timeline(self, project_id: str, username: str) -> dict:
		return self._insights.get_timeline(project_id, username)

	def get_benchmark_metrics(self, project_id: str, session_id: int | None = None) -> list[dict]:
		return self._insights.get_benchmark_metrics(project_id, session_id)

	def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
		return self._insights.set_message_expected_state(message_id, expected_state)

	def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
		return self._insights.set_message_expected_signals(message_id, expected_values)

	def set_message_comment(self, message_id: int, comment: str | None) -> dict | None:
		return self._insights.set_message_comment(message_id, comment)

	def set_message_reaction(self, message_id: int, reaction: str | None) -> dict | None:
		return self._insights.set_message_reaction(message_id, reaction)

	def clear_session_annotations(self, session_id: int) -> None:
		self._insights.clear_session_annotations(session_id)

	def get_latest_signals(self) -> list[SignalPayload]:
		return self._tracking_service.get_latest_signals()

	def get_input_token_budget_per_turn(self) -> int | None:
		return self._tracking_service.get_input_token_budget_per_turn()

	def get_total_token_budget_per_session(self) -> int | None:
		return self._tracking_service.get_total_token_budget_per_session()

	def is_auto_tracking_enabled(self, session_id: int) -> bool:
		self._ownership.require_own_session(session_id)
		return self._tracking_service.is_auto_tracking_enabled(session_id)

	def set_auto_tracking_enabled(self, session_id: int, enabled: bool) -> None:
		self._ownership.require_own_session(session_id)
		self._tracking_service.set_auto_tracking_enabled(session_id, enabled)

	def _with_manual_actions(self, session_id: int, state_payload: dict) -> dict:
		auto_tracking_enabled = self.is_auto_tracking_enabled(session_id)
		return {**state_payload, "manual_actions": manual_actions_for(state_payload["actions"], auto_tracking_enabled)}

	def is_actuators_enabled(self, session_id: int) -> bool:
		self._ownership.require_own_session(session_id)
		return self._actuator_factory.is_enabled_for_test_session(session_id)

	def set_actuators_enabled(self, session_id: int, enabled: bool) -> None:
		self._ownership.require_own_session(session_id)
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
		return self._ownership.require_session(session_id)["project_id"]

	def _apply_declared_env_defaults(self, automaton: Automaton, project_id: str, session_id: int) -> None:
		action = automaton.init_action
		if not action.env:
			return
		env = self._env_for_session(session_id)
		current = env.action_set()
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
		if session_type != "live":
			return
		env = self._env_for_session(session_id)
		orphans = set(env.action_set()) - automaton.declared_env_key_names()
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
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		if session["type"] == "imported":
			return None, None

		project_id = session["project_id"]
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(session_id, session)

		self._apply_declared_env_defaults(automaton, project_id, session_id)
		self._cleanup_orphan_action_env_keys(automaton, project_id, session_id, session["type"])

		if self._db.get_current_state(project_id) is None:
			action = automaton.init_action
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
	) -> tuple[list[dict], dict | None]:
		should_open = not is_self_loop and self._should_generate_opening_message(session_id, new_state)
		if not should_open:
			return [], None
		turn_result = await self._process_turn_body(session_id)
		message_id = turn_result["assistant_message_id"]
		if message_id is None:
			return [], turn_result["state"]
		message = self._db.get_message(message_id)
		return ([message] if message is not None else []), turn_result["state"]

	async def apply_manual_action(self, action_name: str, session_id: int) -> dict:
		project_id = self._project_id_for_session(session_id)
		self._ensure_project_available(project_id)
		if self._session_locks.get(str(session_id)).locked():
			raise ChatServiceError(
				"A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT, code="turn_in_progress",
			)
		async with self._session_scope(project_id, session_id):
			_, source_state = self._project_service.get_automaton_and_state_for_session(session_id)
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
			reply, fresh_state_payload = await self._messages_for_transition(
				session["id"], state, is_self_loop=(action.target == source_state_key)
			)
			self._session_manager.touch_session(session["id"], state.key)
			return {
				"state": fresh_state_payload if fresh_state_payload is not None else self._with_manual_actions(session["id"], state_payload),
				"reply": reply,
				"ai_model": self.get_ai_models_info(),
				"session_id": session["id"],
			}

	def accept_user_message(self, session_id: int, text: str) -> int:
		"""Persists a user message the moment its frame is read — before any
		processing, ahead of the session lock — so the order of the messages
		is the order they arrived on the wire (see WsNotifications). Runs
		the same checks a turn runs, so a message for a closed, foreign or
		paused session is refused rather than stored."""
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND, code="session_not_found")
		project_id = session["project_id"]
		self._ensure_project_available(project_id)
		_, state = self._get_automaton_and_state_or_raise_unsupported(session_id, session)
		self._require_active_session(session_id, project_id, state.key)
		if not state.chat:
			raise ChatServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT,
				code="state_not_chat",
			)
		return self._db.save_message("user", text, session_id)

	async def process_turn(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		user_message_id: int | None = None,
	) -> dict:
		project_id = self._project_id_for_session(session_id)
		async with self._session_scope(project_id, session_id):
			return await self._process_turn_body(session_id, text, on_metadata, user_message_id)

	async def _process_turn_body(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		user_message_id: int | None = None,
	) -> dict:
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		project_id = session["project_id"]
		self._ensure_project_available(project_id)
		ai_service = self._ai_test_service if session["type"] == "test" else self._ai_service
		_, state = self._get_automaton_and_state_or_raise_unsupported(session_id, session)
		self._require_active_session(session_id, project_id, state.key)
		reply = await self._tracking_service._process(
			session_id, text, ai_service, on_metadata, user_message_id=user_message_id,
		)
		self._session_manager.touch_session(reply['session_id'], reply['state']['key'])
		reply['state'] = self._with_manual_actions(session_id, reply['state'])
		return reply
