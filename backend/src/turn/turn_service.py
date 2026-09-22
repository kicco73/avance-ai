from __future__ import annotations

import asyncio
import json

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from http import HTTPStatus

from typing import Any

from automaton.automaton import Action, Automaton, SignalPayload, State, pressable_actions
from automaton.build_error import AutomatonBuildError
from automaton.choice import ChoiceSelection, button_name, option_of
from db import Db, _utc_iso
from config import REPLY_SILENCE_SECONDS
from system.keyed_lock_registry import KeyedLockRegistry
from system.project_locks import ProjectLocks
from project.archive.layout import CACHE_DIR
from system.web_session import WebSession
from system import bus
from system.bus import POINT_SESSION_SERVICES, POINT_TRANSLATABLE_LABELS, TURN_TRANSLATION, Message

from tracking.session_services import SessionServices
from tracking.translatable_labels import TranslatableLabels

from tracking.actuators import TaskNamespace, TaskNamespaceFactory
from tracking.env import Env
from tracking.sources.websearch import websearch_archive_for
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts
from turn.sessions.env_for_session import env_for_session
from turn.ephemeral_env_registry import EphemeralEnvRegistry
from turn.errors import TurnServiceError
from turn.atomic_turn_transaction import AtomicTurnTransaction
from turn.input_processor import (
	InputProcessor, SystemInputProcessor, committed, fragments_of, plain_reply_result, processors as input_processors,
)
from turn.turn_transaction import Inbox, Outbox, PendingMessage, TurnTransaction
from turn.sessions.session_manager import SessionManager, SessionNotWritable
from turn.sessions.session_insights import SessionInsights
from turn.sessions.session_ownership import SessionOwnership
from turn.sessions.session_report_task import SessionReportHydrator, SessionReportScheduler, SessionReportTask
from turn.sessions.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from system.logging_factory import LoggerFactory
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.turn_callbacks import OnMetadata
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from scheduler import SchedulerService
from tracking.tracking_service import TrackingService

logger = LoggerFactory.get_logger(__name__)

class TurnService(object):
	# XXX FOR CLAUDE CODE: an exchange never reaches the Db. A choice taken, a
	def __init__(
		self,
		db: Db,
		ai_service: Any,
		ai_test_service: Any,
		project_service: ProjectService,
		session_manager: SessionManager,
		tracking_service: TrackingService,
		metric_service: MetricService,
		scheduler_service: SchedulerService,
		namespace_factory: TaskNamespaceFactory,
		project_locks: ProjectLocks | None = None,
		reply_silence_seconds: float = REPLY_SILENCE_SECONDS,
	) -> None:
		self._db = db
		self.reply_silence_seconds = reply_silence_seconds
		self._ai_service = ai_service
		self._ai_test_service = ai_test_service
		self.__project_service = project_service
		self.__session_manager = session_manager
		self._tracking_service = tracking_service
		self.metric_service = metric_service
		self._namespace_factory = namespace_factory
		session_report_hydrator = SessionReportHydrator(db, ai_service)
		scheduler_service.register_task_type(SessionReportTask.TYPE, session_report_hydrator.hydrate)
		session_manager.set_session_report_scheduler(SessionReportScheduler(scheduler_service, session_report_hydrator))
		session_manager.set_automaton_starter(self)
		self._ownership = SessionOwnership(db)
		self._insights = SessionInsights(db, metric_service, tracking_service, self._ownership)
		self._user_facts = UserFacts(db)

		self._project_locks = project_locks or ProjectLocks()
		self._session_locks = KeyedLockRegistry(asyncio.Lock)
		self._inboxes: dict[int, Inbox] = {}
		self._outboxes: dict[int, Outbox] = {}
		self._processors: dict[str, InputProcessor] = {name: kind(self) for name, kind in input_processors().items()}
		self._session_lifecycle_locks = KeyedLockRegistry(asyncio.Lock)
		self._global_lock = asyncio.Lock()

		self._choice_translations: dict[int, dict[str, dict[str, str]]] = {}
		bus.contribute(POINT_TRANSLATABLE_LABELS, self._contribute_choice_labels)
		bus.subscribe(TURN_TRANSLATION, self._on_translation)

	def _ai_service_for_session(self, session_id: int) -> Any:
		session = self._db.get_chat_session(session_id)
		return self._ai_test_service if session is not None and session["type"] in ("test", "preview") else self._ai_service

	def _inbox(self, session_id: int) -> Inbox:
		return self._inboxes.setdefault(session_id, Inbox(session_id))

	def outbox(self, session_id: int) -> Outbox:
		return self._outboxes.setdefault(session_id, Outbox())

	def processor_for(self, state: State) -> InputProcessor:
		processor = self._processors.get(state.input_processor)
		if processor is None:
			raise TurnServiceError(
				f"State '{state.key}' needs the '{state.input_processor}' input processor, which isn't "
				"installed in this build.", status_code=HTTPStatus.SERVICE_UNAVAILABLE, code="input_processor_not_installed",
			)
		return processor

	def script_reply_processor(self) -> InputProcessor:
		return self._processors[SystemInputProcessor.name]

	def _processor_at(self, session_id: int) -> InputProcessor:
		_, state = self.__project_service.get_automaton_and_state_for_session(session_id)
		return self.processor_for(state)

	@property
	def tracking_service(self) -> TrackingService:
		return self._tracking_service

	def ai_service_for_session_type(self, session_type: str) -> Any:
		return self._ai_test_service if session_type == "test" else self._ai_service

	def project_id_for_session(self, session_id: int) -> str:
		return self._project_id_for_session(session_id)

	def ensure_project_available(self, project_id: str) -> None:
		self._ensure_project_available(project_id)

	def refuse_while_answering(self, session_id: int) -> None:
		if self._session_locks.get(str(session_id)).locked():
			raise TurnServiceError(
				"A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT, code="turn_in_progress",
			)

	def session_scope(self, project_id: str, session_id: int):
		return self._session_scope(project_id, session_id)

	def automaton_and_state_for(self, session_id: int) -> tuple[Automaton, State]:
		return self.__project_service.get_automaton_and_state_for_session(session_id)

	def automaton_and_state_or_raise_unsupported(
		self, session_id: int, session: dict, transaction: TurnTransaction,
	) -> tuple[Automaton, State]:
		return self._get_automaton_and_state_or_raise_unsupported(session_id, session, transaction)

	def require_active_session(self, session_id: int | None, project_id: str, current_state: str) -> dict:
		return self._require_active_session(session_id, project_id, current_state)

	def resolve_manual_action(self, action_name: str, session_id: int):
		return self.__project_service.resolve_manual_action(action_name, session_id)

	def exchange(self, session_id: int, user_messages: list[PendingMessage] | None) -> AtomicTurnTransaction:
		return self._exchange(session_id, user_messages)

	def tracking_engine_for(self, session_id: int, transaction: TurnTransaction) -> tuple[TrackingEngine, "TaskNamespace"]:
		return self._tracking_engine_for_session(session_id, transaction)

	def touch_session(self, session_id: int, state_key: str) -> None:
		self.__session_manager.touch_session(session_id, state_key)

	def _env_for_session(self, session_id: int) -> Env:
		return env_for_session(TurnTransaction(self._db, session_id, []), self._ownership.require_session(session_id))

	def _tracking_engine_for_session(
		self, session_id: int, transaction: TurnTransaction,
	) -> tuple[TrackingEngine, "TaskNamespace"]:
		session = self._ownership.require_session(session_id)
		fixed_context = FixedProjectContext(project_id=session["project_id"])
		env = env_for_session(transaction, session)
		session_facts = SessionFacts(transaction, fixed_context)
		task_namespace = transaction.task_namespace(self._namespace_factory.for_session(session_id))
		chat_namespace = self._namespace_factory.chat_for_session(session_id)
		scope_builder = EvaluationScopeBuilder(
			env, self.metric_service, session_facts, self._user_facts,
			transaction, task_namespace, chat_namespace,
			ai_service=self._ai_service_for_session(session_id), reply=self.outbox(session_id),
		)
		return TrackingEngine(DbTrackingSink(transaction), env, scope_builder), task_namespace

	def _schedule_task(self, automaton: Automaton, action: Action, session_id: int, project_id: str) -> None:
		if not action.task:
			return
		tracking_engine, _ = self._tracking_engine_for_session(session_id, TurnTransaction(self._db, session_id, []))
		tracking_engine.schedule_task(automaton, action, action.target, ChoiceSelection.NONE, session_id=session_id)

	@property
	def _active_project_id(self) -> str:
		return self.__project_service.get_active_project_id()

	@property
	def _username(self) -> str:
		return WebSession().user

	def get_message_audio_text(self, message_id: int) -> str | None:
		return self._db.get_message_audio_text(message_id)

	_NO_AI_MODELS: dict = {"auto": True, "current_index": None, "models": []}

	def get_ai_models_info(self) -> dict:
		return self._ai_service.get_models_info() if self._ai_service is not None else self._NO_AI_MODELS

	def select_ai_model(self, index: int | None) -> None:
		if self._ai_service is not None:
			self._ai_service.select_model(index)

	def get_test_ai_models_info(self) -> dict:
		return self._ai_test_service.get_models_info() if self._ai_test_service is not None else self._NO_AI_MODELS

	def select_test_ai_model(self, index: int | None) -> None:
		if self._ai_test_service is not None:
			self._ai_test_service.select_model(index)

	def _session_payload(self, session: dict, *, current: bool) -> dict:
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
			"open": self.__session_manager.is_open(session),
			"current": current,
			"has_annotations": session["labeled"],
			"comment": session["comment"],
			"ai_summary": session["ai_summary"],
		}

	def _session_revision_unsupported(self, session: dict) -> bool:
		if session["type"] == "test":
			return False
		try:
			self.__project_service.get_automaton(session["project_id"], session["project_revision"])
		except (AutomatonBuildError, FileNotFoundError, ValueError):
			return True
		return False

	def _ensure_project_available(self, project_id: str) -> None:
		is_paused, paused_reason = self.__project_service.get_project_availability(project_id)
		if is_paused:
			raise TurnServiceError(
				paused_reason or "This project is currently paused.",
				status_code=HTTPStatus.CONFLICT, code="project_unavailable",
			)

	def _get_automaton_and_state_or_raise_unsupported(
		self, session_id: int, session: dict, transaction: TurnTransaction,
	) -> tuple[Automaton, State]:
		try:
			return self.__project_service.get_automaton_and_state_as_recorded(
				session_id, transaction.get_current_state_for_session(session_id),
			)
		except (AutomatonBuildError, FileNotFoundError, ValueError) as exc:
			if session["type"] == "test":
				raise
			raise TurnServiceError(
				f"This session is pinned to revision {session['project_revision']}, which this version of "
				"Avance can no longer run.",
				status_code=HTTPStatus.CONFLICT, code="session_revision_unsupported",
			) from exc

	def _require_active_session(self, session_id: int | None, project_id: str, current_state: str) -> dict:
		try:
			return self.__session_manager.require_active_session(
				self._username, project_id, session_id, current_state
			)
		except SessionNotWritable as exc:
			raise TurnServiceError(str(exc), status_code=HTTPStatus.CONFLICT, code=exc.code) from exc

	def get_legal_terms_status(self, project_id: str) -> dict:
		return self.__project_service.get_legal_terms_status(self._username, project_id)

	def accept_legal_terms(self, project_id: str) -> None:
		self.__project_service.accept_legal_terms(self._username, project_id)

	def _legal_terms_pending_response(self, project_id: str) -> dict | None:
		if self.__project_service.legal_terms_pending(self._username, project_id):
			return {"legal_terms_pending": True, "project_id": project_id}
		return None

	def _session_response(self, session: dict, *, current: bool) -> dict:
		automaton, state = self.__project_service.get_automaton_and_state_for_session(session["id"])
		return {**self._session_payload(session, current=current), "state": automaton.get_state_payload(state)}

	async def _get_current_session_if_any_or_create_new_of_type(
		self, strategy: SessionTypeStrategy, project_id: str, session_id: int | None
	) -> dict:
		async with self._session_lifecycle_scope(self._username, project_id):
			try:
				if strategy.type_name == 'live' and self.__session_manager.get_active_session(self._username, project_id) is None:
					pending = self._legal_terms_pending_response(project_id)
					if pending is not None:
						return pending
				_, state = self.__project_service.get_automaton_and_state(
					project_id, type=strategy.type_name, username=self._username
				)
				session = self.__session_manager.get_current_session_if_any_or_create_new(
					strategy, self.__project_service, self._username, project_id, session_id, state.key
				)
			except ValueError as exc:
				raise TurnServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return self._session_response(session, current=True)

	def session_named(self, session_id: int) -> dict:
		"""That very conversation, for whoever was handed its id rather
		than a project: an operator paged into one, a person picking a
		past one out of the list."""
		session = self._ownership.require_own_session(session_id)
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(
			session_id, session, TurnTransaction(self._db, session_id, []),
		)
		return {
			**self._session_payload(session, current=True),
			"state": automaton.get_state_payload(state),
		}

	async def enter_session(self, project_id: str, type: str) -> dict:
		"""The conversation of that kind this person is having in that
		project, made if there is none. The project is named by whoever
		is showing the chat: reading the active project here is what let
		a preview of one app open the chat of another."""
		is_paused, paused_reason = self.__project_service.get_project_availability(project_id)
		if is_paused:
			return {"blocked": "paused", "detail": paused_reason or ""}
		return await self._get_current_session_if_any_or_create_new_of_type(
			get_session_type_strategy(type), project_id, None,
		)

	async def create_session_of(self, project_id: str, type: str) -> dict:
		is_paused, paused_reason = self.__project_service.get_project_availability(project_id)
		if is_paused:
			return {"blocked": "paused", "detail": paused_reason or ""}
		return await self._create_session_of_type(get_session_type_strategy(type), project_id)

	async def acquire_exclusive_session(self) -> dict:
		project_id = self._active_project_id
		is_paused, paused_reason = self.__project_service.get_project_availability(project_id)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		async with self._session_lifecycle_scope(self._username, project_id):
			if self.__session_manager.get_active_session(self._username, project_id) is None:
				pending = self._legal_terms_pending_response(project_id)
				if pending is not None:
					return pending
			_, state = self.__project_service.get_automaton_and_state(project_id, type='live', username=self._username)
			try:
				session = self.__session_manager.acquire_exclusive_session(
					get_session_type_strategy('live'), self.__project_service, self._username, project_id, state.key
				)
			except ValueError as exc:
				raise TurnServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return self._session_response(session, current=True)

	async def record_unsolicited_reply(self, username: str, project_id: str, content: str) -> None:
		"""An assistant message a channel sent on its own initiative,
		written to that user's session so the transcript still holds it.
		The session it lands on is the active one, or a new one opened on
		whatever channel the caller has set — this service does not know
		which channels exist, let alone which one is speaking."""
		async with self._session_lifecycle_scope(username, project_id):
			session = self.__session_manager.get_active_session(username, project_id)
			if session is None:
				session = self.__session_manager.create_session(
					get_session_type_strategy('live'), self.__project_service, username, project_id,
				)
			self._db.save_message('assistant', content, session["id"])

	async def _create_session_of_type(self, strategy: SessionTypeStrategy, project_id: str) -> dict:
		async with self._session_lifecycle_scope(self._username, project_id):
			try:
				if strategy.type_name == 'live':
					if self.__project_service.legal_terms_pending(self._username, project_id):
						return {"legal_terms_pending": True, "project_id": project_id}
					active = self.__session_manager.get_active_session(self._username, project_id)
					if active is not None:
						reason = "force-new-session" if active["channel"] == strategy.caller_channel() else "channel-switch"
						self.__session_manager.close_session(active, reason)
				session = self.__session_manager.create_session(
					strategy, self.__project_service, self._username, project_id
				)
			except ValueError as exc:
				raise TurnServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return self._session_response(session, current=True)

	def reset_test_sessions(self, project_id: str) -> dict:
		reset_session_ids = [
			session["id"] for session in self._db.list_chat_sessions(self._username, project_id, type='test')
		]
		self.__project_service.reset_test_sessions(project_id)
		for reset_id in reset_session_ids:
			EphemeralEnvRegistry().discard(reset_id)
			self._db.delete_archives_with_prefix(project_id, f"{CACHE_DIR}/sessions/{reset_id}/")
		automaton, state = self.__project_service.get_automaton_and_state(project_id, type='test')
		return automaton.get_state_payload(state)

	def _list_sessions_by_type(self, project_id: str, type: str | tuple[str, ...], active_type: str) -> list[dict]:
		sessions = self._db.list_chat_sessions(None, project_id, type=type)
		sessions = [s for s in sessions if self._ownership.owns_session(s['username'])]
		active = self.__session_manager.get_active_session(self._username, project_id, type=active_type)
		return [
			{
				**self._session_payload(s, current=get_session_type_strategy(s["type"]).is_current(s, active)),
				"unsupported_revision": self._session_revision_unsupported(s),
			}
			for s in sessions
		]

	def list_sessions(self, project_id: str, include_imported: bool = False) -> list[dict]:
		type = ('live', 'imported') if include_imported else 'live'
		return self._list_sessions_by_type(project_id, type, active_type='live')

	def list_test_sessions(self, project_id: str) -> list[dict]:
		return self._list_sessions_by_type(project_id, 'test', active_type='test')

	async def delete_session(self, session_id: int) -> None:
		self._ownership.require_own_session(session_id)
		project_id = self._project_id_for_session(session_id)
		async with self._session_scope(project_id, session_id):
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
			self.__session_manager.close_session(session, "manual-user")
		EphemeralEnvRegistry().discard(session_id)
		self._inboxes.pop(session_id, None)
		return self._reloaded_session_payload(session_id)

	async def close_exhausted_session(self, session_id: int) -> None:
		session = self._db.get_chat_session(session_id)
		assert session is not None
		async with self._session_lifecycle_scope(session["username"], session["project_id"]):
			self.__session_manager.close_session(session, "final-state")
		EphemeralEnvRegistry().discard(session_id)
		self._inboxes.pop(session_id, None)

	def get_session_rating(self, session_id: int) -> int | None:
		session = self._ownership.require_own_session(session_id)
		return self._db.get_app_rating(session["username"], session["project_id"], session["project_revision"])

	def rate_session(self, session_id: int, rating: int) -> int:
		session = self._ownership.require_own_session(session_id)
		self._db.set_app_rating(session["username"], session["project_id"], session["project_revision"], rating, session_id=session_id)
		return rating

	def _reloaded_session_payload(self, session_id: int) -> dict:
		session = self._db.get_chat_session(session_id)
		assert session is not None
		strategy = get_session_type_strategy(session["type"])
		active_session = self.__session_manager.get_active_session(self._username, session["project_id"], type=session["type"])
		return self._session_payload(session, current=strategy.is_current(session, active_session))

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
			_, state = self.__project_service.get_automaton_and_state_for_session(session_id)
			self._db.touch_chat_session(session_id, latest or session["datetime_start"], state.key)

	def get_state_for_session(self, session_id: int) -> dict:
		self._ownership.require_own_session(session_id)
		session = self._db.get_chat_session(session_id)
		assert session is not None
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(
			session_id, session, TurnTransaction(self._db, session_id, []),
		)
		return automaton.get_state_payload(state)

	def read_history(self, session_id: int, last_n: int | None = None) -> list[dict]:
		"""What is already there, and nothing else — every reader of a
		transcript, with no exception left.

		There used to be a second one that opened the conversation first,
		as a side effect of being asked for the history, and every caller
		of it got a real turn it had not asked for. Once a browser began
		saying `session.enter` (see docs/BUS.md), that was two openings for
		one conversation and a session that started by saying the same
		thing twice. Opening a conversation is something a channel does on
		purpose; reading it is a read."""
		self._ownership.require_own_session(session_id)
		return self._with_tool_calls(
			session_id, self._db.get_messages(session_id, last_n=last_n) + self._inbox(session_id).pending(),
		)

	def _with_tool_calls(self, session_id: int, messages: list[dict]) -> list[dict]:
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
		automaton = self.__project_service.get_automaton_for_session(session_id)
		return {
			"memory": env.memory(until),
			"action_set": env.action_set(until),
			"ai_definition": {env_key.name: env_key.ai_definition for env_key in automaton.env_keys},
		}

	def get_websearch_cache(self, session_id: int) -> dict:
		self._ownership.require_own_session(session_id)
		automaton = self.__project_service.get_automaton_for_session(session_id)
		return {"content": websearch_archive_for(self._db, automaton, session_id).read()}

	def get_output(self, session_id: int, message_id: int | None = None) -> dict:
		"""This turn's own raw structured `output` field values (see
		Tracking.output) — kept purely for observability by the Run Inspector's
		Output card. message_id ties it to one specific chat line, same
		row get_env's own "until" reconstruction can't reuse: unlike env
		(cumulative across the whole session), output is a single turn's
		own snapshot, so there's nothing to replay — just the one linked
		row (or, with no message selected, the session's latest)."""
		self._ownership.require_own_session(session_id)
		if message_id is not None:
			row = self._db.get_signal_row_by_message(message_id)
		else:
			rows = [r for r in self._db.get_signals(session_id) if r.get("output")]
			row = rows[-1] if rows else None
		return {"output": json.loads(row["output"]) if row and row.get("output") else {}}

	def set_env_value(self, session_id: int, key: str, value: str) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).set_value(key, value)
		return self.get_env(session_id)

	def delete_env_key(self, session_id: int, key: str) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).delete_key(key)
		return self.get_env(session_id)

	def clear_memory(self, session_id: int) -> dict:
		self._ownership.require_own_session(session_id)
		self._env_for_session(session_id).clear_memory()
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

	def is_audio_enabled(self, session_id: int) -> bool:
		self._ownership.require_own_session(session_id)
		return self._tracking_service.is_audio_enabled(session_id)

	def set_audio_enabled(self, session_id: int, enabled: bool) -> None:
		self._ownership.require_own_session(session_id)
		self._tracking_service.set_audio_enabled(session_id, enabled)

	def services_for(self, session_id: int) -> dict[str, bool]:
		"""What this conversation can reach, asked of the session's own
		project rather than of whichever project the person has active
		(see tracking/session_services.py)."""
		automaton = self.__project_service.get_automaton_for_session(session_id)
		return bus.collect(POINT_SESSION_SERVICES, SessionServices(services=automaton.services)).available

	def buttons_for(self, session_id: int, state_payload: dict) -> list[dict]:
		"""What this state offers the person to press. Never folded into
		the state payload: the choices are their own message (`state.buttons`,
		see docs/BUS.md), and a state that carried them too meant two
		roads to the same buttons and a first paint that disagreed with
		what was published."""
		pressable = pressable_actions(state_payload["actions"])
		return pressable + self._choice_buttons_for(session_id, state_payload["key"])

	def _choice_buttons_for(self, session_id: int, state_key: str) -> list[dict]:
		automaton = self.__project_service.get_automaton_for_session(session_id)
		descriptions = {env_key.name: env_key.ai_definition for env_key in automaton.env_keys}
		options_by_key = self.choice_options_for(session_id)
		translations = self._choice_translations.get(session_id, {})
		return [
			{
				"name": button_name(key, index),
				**option_of(option).button_fields(translations.get(key, {})),
				"ui_description": descriptions.get(key),
				"target": "",
				"has_trigger": False,
				"task": None,
				"on-exit": None,
			}
			for key in automaton.states[state_key].choice_keys
			for index, option in enumerate(options_by_key.get(key, []))
		]

	def _contribute_choice_labels(self, target: TranslatableLabels) -> None:
		automaton = self.__project_service.get_automaton_for_session(target.session_id)
		state = automaton.states.get(target.state_key)
		if state is None or not state.choice_keys:
			return
		options_by_key = self.choice_options_for(target.session_id)
		for key in state.choice_keys:
			for option in options_by_key.get(key, []):
				for text in option_of(option).translatable_texts():
					target.contribute(key, text)

	async def _on_translation(self, message: Message) -> None:
		key, text, translation = message.body["key"], message.body["text"], message.body["translation"]
		self._choice_translations.setdefault(message.session_id, {}).setdefault(key, {})[text] = translation

	def choice_options_for(self, session_id: int) -> dict[str, list[str | dict]]:
		automaton = self.__project_service.get_automaton_for_session(session_id)
		declared = {env_key.name for env_key in automaton.env_keys if env_key.type == "list"}
		current = self._env_for_session(session_id).action_set()
		return {
			key: list(options) for key, options in current.items() if key in declared and isinstance(options, list)
		}

	def is_actuators_enabled(self, session_id: int) -> bool:
		self._ownership.require_own_session(session_id)
		return self._namespace_factory.is_enabled_for_test_session(session_id)

	def set_actuators_enabled(self, session_id: int, enabled: bool) -> None:
		self._ownership.require_own_session(session_id)
		self._namespace_factory.set_enabled_for_test_session(session_id, enabled)

	def global_exclusive_access(self):
		return self._global_lock

	def acquire_read(self, project_id: str):
		return self._project_locks.acquire_read(project_id)

	def acquire_write(self, project_id: str):
		return self._project_locks.acquire_write(project_id)

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

	def _backfill_declared_env_keys(
		self, automaton: Automaton, project_id: str, session_id: int, username: str
	) -> None:
		action = automaton.env_defaults_action
		if not action.env:
			return
		env = self._env_for_session(session_id)
		current = env.action_set()
		missing = {key: expression for key, expression in action.env.items() if key not in current}
		if not missing:
			return
		tracking_engine, _ = self._tracking_engine_for_session(session_id, TurnTransaction(self._db, session_id, []))
		for key, expression in missing.items():
			tracking_engine.apply_action_env(
				automaton, replace(action, env={key: expression}), {}, ChoiceSelection.NONE, "",
				username=username, project_id=project_id, session_id=session_id,
			)

	def start_automaton(self, strategy: SessionTypeStrategy, session: dict, username: str) -> None:
		project_id = session["project_id"]
		fires = strategy.fires_init_action(self.__project_service, project_id, username)
		for _ in filter(None, [fires]):
			self._restart_automaton(session, username)

	def _restart_automaton(self, session: dict, username: str) -> None:
		session_id, project_id = session["id"], session["project_id"]
		env_for_session(TurnTransaction(self._db, session["id"], []), session).clear()
		automaton = self.__project_service.get_automaton_for_session(session_id)
		self._backfill_declared_env_keys(automaton, project_id, session_id, username)
		tracking_engine, _ = self._tracking_engine_for_session(session_id, TurnTransaction(self._db, session_id, []))
		tracking_engine.apply_transition(
			automaton, automaton.states[""], automaton.init_action, None, ChoiceSelection.NONE, session_id,
			origin='init-action', username=username, project_id=project_id,
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
			raise TurnServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		if session["type"] == "imported":
			return None, None

		project_id = session["project_id"]
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(
			session_id, session, TurnTransaction(self._db, session_id, []),
		)

		self._backfill_declared_env_keys(automaton, project_id, session_id, self._username)
		self._cleanup_orphan_action_env_keys(automaton, project_id, session_id, session["type"])

		return automaton, state

	async def open_conversation(self, session_id: int, on_metadata: OnMetadata | None = None) -> dict | None:
		automaton, _ = await self._ensure_project_bootstrap(session_id)
		for _ in filter(None, [automaton is None]):
			return None
		return await self.process_turn(session_id, on_metadata=on_metadata)

	async def prepare_user_initiated_turn(self, session_id: int) -> list[dict]:
		"""The project bootstrap a user-initiated turn needs, plus the
		wrap-up message of a state that cannot take a turn at all — the
		only thing such a session would ever say. Returns whatever it
		persisted, because a caller that reports a turn has to report
		this too: it happened as part of the same exchange, and the turn
		that follows will not report it — a turn response carries exactly
		one assistant message, its own (see TrackingProcessor.
		_build_turn_response). Returned rather than folded into the turn
		because the turn can fail: the wrap-up is persisted either way and
		the person is owed it either way."""
		automaton, state = await self._ensure_project_bootstrap(session_id)
		if automaton is None:
			return []
		if not (state.final or not state.chat_enabled):
			return []
		if not self._state_speaks_unprompted(session_id, state):
			return []
		result = await self.process_turn(session_id)
		return list(result["reply"])

	def _state_speaks_unprompted(self, session_id: int, state: State) -> bool:
		if self._namespace_factory.get_human_operator(session_id) is not None:
			return False
		content_since = self._db.history_cutoff_for_session(session_id, state.history_cutoff)
		chat_blocked = state.final or not state.chat_enabled
		gate_since = self._db.get_last_transition_timestamp_for_session(session_id) if chat_blocked else content_since
		return not self._db.has_messages_since(session_id, gate_since)

	async def apply_manual_action(
		self, action_name: str, session_id: int, on_metadata: OnMetadata | None = None,
	) -> dict:
		return await self._processor_at(session_id).manual_action(action_name, session_id, on_metadata)

	async def apply_choice(
		self, selection: ChoiceSelection, session_id: int, on_metadata: OnMetadata | None = None,
	) -> dict | None:
		return await self._processor_at(session_id).choice(selection, session_id, on_metadata)

	def accept_user_message(self, session_id: int, text: str) -> PendingMessage:
		"""Persists a user message the moment its frame is read — before any
		processing, ahead of the session lock — so the order of the messages
		is the order they arrived on the wire (see BusChannel). Runs
		the same checks a turn runs, so a message for a closed, foreign or
		paused session is refused rather than stored."""
		session = self._db.get_chat_session(session_id)
		if session is None:
			raise TurnServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND, code="session_not_found")
		project_id = session["project_id"]
		self._ensure_project_available(project_id)
		_, state = self._get_automaton_and_state_or_raise_unsupported(
			session_id, session, TurnTransaction(self._db, session_id, []),
		)
		self._require_active_session(session_id, project_id, state.key)
		if not state.chat_enabled:
			raise TurnServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT,
				code="state_not_chat",
			)
		return self._inbox(session_id).accept(text)

	async def process_turn(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		user_messages: list[PendingMessage] | None = None,
	) -> dict:
		"""`user_messages` are the messages this answer is for, already
		persisted by whoever accepted them (see turn/input_listener.py).
		Any other caller hands over the text and it is persisted here,
		this message (the websocket does, the moment it read the frame —
		see BusChannel); any other caller hands over the text and it
		is persisted here, still before the session lock, so the bubble
		appears at once and the order of the conversation is fixed before
		anything waits."""
		if text is not None and not user_messages:
			user_messages = [self.accept_user_message(session_id, text)]
		operator = self._namespace_factory.get_human_operator(session_id)
		if operator is not None:
			return await self._process_human_turn(session_id, operator, on_metadata, user_messages)
		return await self._processor_at(session_id).text(session_id, text, on_metadata, user_messages)

	async def _process_human_turn(
		self, session_id: int, operator: str, on_metadata: OnMetadata | None, user_messages: list[PendingMessage] | None,
	) -> dict:
		"""chat.switch_to_human's own turn path — no automaton, no
		lock: while a session has an operator (see TaskNamespaceFactory.
		get_human_operator) it isn't an automaton-driven conversation at
		all, so none of TrackingEngine/_session_scope applies. The
		operator's reply can take anywhere from seconds to minutes;
		nothing else about this session (another customer message, a
		manual action) should have to wait for it, which is exactly what
		holding _session_scope's lock here would do."""
		transaction = self._exchange(session_id, user_messages)
		session = transaction.get_chat_session(session_id)
		if session is None:
			raise TurnServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
		project_id = session["project_id"]
		self._ensure_project_available(project_id)
		automaton, state = self._get_automaton_and_state_or_raise_unsupported(session_id, session, transaction)
		async with transaction:
			fragments = fragments_of(transaction, user_messages)
			text = "\n".join(m["content"] for _, m in fragments)
			assistant_talker = self._tracking_service.build_human_talker(operator, session_id, session["type"], project_id)
			accumulated = ""
			async for chunk in assistant_talker.chat([], [{"role": "user", "content": text}], on_metadata or (lambda key, value: None)):
				if on_metadata is None:
					continue
				if chunk:
					on_metadata("chunk", chunk)
					accumulated += chunk
				else:
					on_metadata("typing", None)
			assistant_message = transaction.save_message("assistant", accumulated, session_id)
			transaction.mark_messages_answered([h for h, _ in fragments], assistant_message)
			self.__session_manager.touch_session(session_id, state.key)
		return committed(transaction, plain_reply_result(
			session_id, automaton, state, assistant_message, user_messages,
			self.buttons_for(session_id, automaton.get_state_payload(state)), self.get_ai_models_info(),
		))

	def _exchange(self, session_id: int, user_messages: list[PendingMessage] | None) -> AtomicTurnTransaction:
		return AtomicTurnTransaction(self._db, session_id, user_messages or [], self._inbox(session_id))
