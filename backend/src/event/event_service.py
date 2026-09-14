"""Cross-project wake-up: when a project's state/env changes for a user,
every OTHER project referencing it via automaton.* that the same user has
ever talked to gets a chance to re-evaluate its triggers. One handler
serves both event types — neither carries anything the other doesn't."""
from __future__ import annotations

from ai import AiService
from automaton.automaton import pressable_actions
from db.db import Db
from events import EnvChanged, StateChanged, subscribe, unsubscribe
from jobs import CancelableJob
from system import bus
from system.bus import UI_NOTIFICATION, Message
from system.logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from scheduler import SchedulerService
from system.web_session import WebSession
from tracking.actuators import TaskNamespaceFactory
from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.tracking_service import TrackingService
from tracking.user_facts import UserFacts

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
        tracking_service: TrackingService | None = None,
        ai_service: AiService | None = None,
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._scheduler_service = scheduler_service
        self._namespace_factory = namespace_factory
        self._tracking_service = tracking_service
        self._ai_service = ai_service

    def register(self) -> None:
        subscribe(StateChanged, self._on_event)
        subscribe(EnvChanged, self._on_event)

    def unregister(self) -> None:
        unsubscribe(StateChanged, self._on_event)
        unsubscribe(EnvChanged, self._on_event)

    def _on_event(self, event: StateChanged | EnvChanged) -> None:
        try:
            for observer_project_id in self._db.get_observers(event.project_id):
                if self._db.get_latest_chat_session(event.username, observer_project_id) is not None:
                    self._wake(event.username, observer_project_id)
        except Exception:
            logger.exception(
                "Wake-up dispatch failed for %s in project '%s'.", type(event).__name__, event.project_id
            )

    async def _reevaluate_and_apply(self, username: str, observer_project_id: str) -> None:
        """Re-derives `observer_project_id`'s own current scope from
        scratch — a fresh Env/MetricService/SessionFacts/UserFacts/AutomatonNamespace
        bound to this (username, observer_project_id) pair — then applies a self-loop transition if one fires."""
        session = self._db.get_latest_chat_session(username, observer_project_id)
        if session is None:
            return

        automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
        with WebSession().impersonate(username):
            project_context = FixedProjectContext(project_id=observer_project_id)
            env = PersistedEnv(self._db, project_context, session["id"])
            metrics = MetricService(self._db, project_context)
            session_facts = SessionFacts(self._db, project_context)
            user_facts = UserFacts(self._db)
            automaton_namespace = AutomatonNamespace(self._db, self._project_service)
            scope_builder = EvaluationScopeBuilder(
                env, metrics, session_facts, user_facts, self._db, automaton_namespace,
                self._namespace_factory.live(project_id=observer_project_id),
                chat_namespace=self._namespace_factory.chat_live(project_id=observer_project_id),
                ai_service=self._ai_service,
            )
            tracking_engine = TrackingEngine(DbTrackingSink(self._db), env, scope_builder)

            scope = scope_builder.build(automaton, state.key, {})
            action = automaton.evaluate_triggers_action(state.key, scope)
            if action is not None and action.target == state.key:
                tracking_engine.apply_transition(
                    automaton, state, action, {}, session["id"],
                    origin='system', username=username, project_id=observer_project_id,
                )
                state_payload = automaton.get_state_payload(state)
                auto_tracking_enabled = (
                    self._tracking_service.is_auto_tracking_enabled(session["id"])
                    if self._tracking_service is not None else True
                )
                await bus.publish(Message(type=UI_NOTIFICATION, username=username, body={
                    "project_name": observer_project_id,
                    "state": state_payload,
                    "buttons": pressable_actions(state_payload["actions"], auto_tracking_enabled),
                }))

    def _wake(self, username: str, observer_project_id: str) -> None:
        self._scheduler_service.submit(WakeupJob(self, username, observer_project_id))
