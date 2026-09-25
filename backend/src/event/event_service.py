"""Cross-project wake-up: when a project's state/env changes for a user,
every OTHER project whose current state watches it via event.* and that
the same user has a conversation in gets a chance to re-evaluate its
triggers. One handler serves both message types — neither carries
anything the other doesn't."""
from __future__ import annotations

from typing import Any

from automaton.choice import ChoiceSelection

from automaton.automaton import pressable_actions
from db.db import Db
from jobs import CancelableJob
from system import bus
from system.bus import ENV_CHANGED, STATE_CHANGED, UI_NOTIFICATION, Message
from system.logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from scheduler import SchedulerService
from system.usage_account import SessionAccount, charged
from system.web_session import WebSession
from tracking.actuators import TaskNamespaceFactory
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.user_facts import UserFacts

from event.event_namespace import watched_from, NAME
from turn.turn_transaction import TurnTransaction

logger = LoggerFactory.get_logger(__name__)


class WakeupJob(CancelableJob):

    def __init__(self, service: "EventService", username: str, observer_project_id: str) -> None:
        super().__init__(key=f"wakeup:{observer_project_id}:{username}", username="system")
        self._service = service
        self._username = username
        self._observer_project_id = observer_project_id

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        await self._service._reevaluate_and_apply(self._username, self._observer_project_id)


class EventService:
    def __init__(
        self, db: Db, project_service: ProjectService, scheduler_service: SchedulerService, namespace_factory: TaskNamespaceFactory,
        ai_service: Any = None,
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._scheduler_service = scheduler_service
        self._namespace_factory = namespace_factory
        self._ai_service = ai_service

    def register(self) -> None:
        for message_type in (STATE_CHANGED, ENV_CHANGED):
            bus.subscribe(message_type, self._on_event)

    def unregister(self) -> None:
        for message_type in (STATE_CHANGED, ENV_CHANGED):
            bus.unsubscribe(message_type, self._on_event)

    async def _on_event(self, message: Message) -> None:
        if message.username is None or message.project_id is None:
            logger.debug("%s carries no user or no project — nobody to wake.", message.type)
            return
        for observer_project_id in self._db.list_projects():
            try:
                if self._watches(message.username, observer_project_id, message.project_id):
                    self._wake(message.username, observer_project_id)
            except Exception:
                logger.exception(
                    "Wake-up dispatch failed for %s in project '%s' towards '%s'.",
                    message.type, message.project_id, observer_project_id,
                )

    def _watches(self, username: str, observer_project_id: str, watched_project_id: str) -> bool:
        session = self._db.get_latest_chat_session(username, observer_project_id)
        if session is None or not self._mentions_events(observer_project_id, session["project_revision"]):
            return False
        automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
        return watched_project_id in watched_from(automaton, state.key)

    def _mentions_events(self, project_id: str, revision: int) -> bool:
        index_yml = self._db.get_archive(project_id, "index.yml", revision=revision)
        return index_yml is not None and f"{NAME}." in index_yml.decode("utf-8", errors="replace")

    async def _reevaluate_and_apply(self, username: str, observer_project_id: str) -> None:
        """Re-derives `observer_project_id`'s own current scope from
        scratch — a fresh Env/MetricService/SessionFacts/UserFacts
        bound to this (username, observer_project_id) pair — then applies a self-loop transition if one fires."""
        session = self._db.get_latest_chat_session(username, observer_project_id)
        if session is None:
            return

        automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
        with WebSession().impersonate(username):
            project_context = FixedProjectContext(project_id=observer_project_id)
            env = PersistedEnv(TurnTransaction(self._db, session["id"], []), project_context, session["id"])
            metrics = MetricService(self._db, project_context)
            session_facts = SessionFacts(self._db, project_context)
            user_facts = UserFacts(self._db)
            scope_builder = EvaluationScopeBuilder(
                env, metrics, session_facts, user_facts, self._db,
                self._namespace_factory.live(project_id=observer_project_id),
                chat_namespace=self._namespace_factory.chat_live(project_id=observer_project_id),
                ai_service=charged(self._ai_service, SessionAccount(session)),
            )
            tracking_engine = TrackingEngine(DbTrackingSink(TurnTransaction(self._db, session["id"], [])), env, scope_builder)

            scope = scope_builder.build(automaton, state.key, {}, ChoiceSelection.NONE)
            action = automaton.evaluate_triggers_action(state.key, scope)
            if action is not None and action.target == state.key:
                tracking_engine.apply_transition(
                    automaton, state, action, {}, ChoiceSelection.NONE, session["id"],
                    origin='system', username=username, project_id=observer_project_id,
                )
                state_payload = automaton.get_state_payload(state)
                await bus.publish(Message(type=UI_NOTIFICATION, username=username, body={
                    "project_name": observer_project_id,
                    "state": state_payload,
                    "buttons": pressable_actions(state_payload["actions"]),
                }))

    def _wake(self, username: str, observer_project_id: str) -> None:
        self._scheduler_service.submit(WakeupJob(self, username, observer_project_id))
