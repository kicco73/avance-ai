"""Cross-project wake-up: when a project's state/env changes for a user,
every OTHER project referencing it via automaton.* that the same user has
ever talked to gets a chance to re-evaluate its triggers. One handler
serves both event types — neither carries anything the other doesn't."""
from __future__ import annotations

from chat.ws_adapter import WsAdapter
from db.db import Db
from events import EnvChanged, StateChanged, subscribe
from jobs import CancelableJob, JobQueue
from logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from session import Session
from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.user_facts import UserFacts

logger = LoggerFactory.get_logger(__name__)


class WakeupJob(CancelableJob):

    def __init__(self, service: "WakeupService", username: str, observer_project_name: str) -> None:
        super().__init__(key=f"wakeup:{observer_project_name}:{username}", username="system")
        self._service = service
        self._username = username
        self._observer_project_name = observer_project_name

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        await self._service._reevaluate_and_apply(self._username, self._observer_project_name)


class WakeupService:
    def __init__(
        self, db: Db, project_service: ProjectService, job_queue: JobQueue, ws_adapter: WsAdapter | None = None,
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._job_queue = job_queue
        # None whenever no websocket transport is configured — push is
        # simply skipped in that case; a re-evaluated self-loop is still
        # applied and persisted either way, only live delivery depends on this.
        self._ws_adapter = ws_adapter

    def register(self) -> None:
        subscribe(StateChanged, self._on_event)
        subscribe(EnvChanged, self._on_event)

    def _on_event(self, event: StateChanged | EnvChanged) -> None:
        # Never lets a wake-up failure propagate back into publish()'s
        # own caller — that caller is always some *other* project's real
        # turn, which must complete regardless of whether waking up an observer succeeds.
        try:
            # The observer index is keyed by the observed project's id
            # (see ProjectObserverIndex), not its name.
            project_id = self._db.get_project_id(event.project_name)
            observers = self._db.get_observers(project_id) if project_id else []
            for observer_project_name in observers:
                if self._db.get_latest_chat_session(event.username, observer_project_name) is not None:
                    self._wake(event.username, observer_project_name)
        except Exception:
            logger.exception(
                "Wake-up dispatch failed for %s in project '%s'.", type(event).__name__, event.project_name
            )

    async def _reevaluate_and_apply(self, username: str, observer_project_name: str) -> None:
        """Re-derives `observer_project_name`'s own current scope from
        scratch — a fresh Env/MetricService/SessionFacts/UserFacts/AutomatonNamespace
        bound to this (username, observer_project_name) pair — then applies a self-loop transition if one fires."""
        session = self._db.get_latest_chat_session(username, observer_project_name)
        if session is None:
            return  # deleted between dispatch and this job actually running

        automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])

        # PersistedEnv/MetricService/SessionFacts/UserFacts/AutomatonNamespace
        # all read Session().user themselves now — pinned to the observer
        # being woken (never whatever's live for this job's own context),
        # then restored. project_context stands in for the *live* active
        # project these would otherwise resolve, staying fixed on
        # observer_project_name instead — a wake-up must never silently
        # evaluate against whatever project happens to be active right now.
        with Session().impersonate(username):
            project_context = FixedProjectContext(project_name=observer_project_name)
            env = PersistedEnv(self._db, project_context)
            metrics = MetricService(self._db, project_context)
            system = SystemFacts()
            session_facts = SessionFacts(self._db, project_context)
            user_facts = UserFacts(self._db)
            # The real project_service here, unlike project_context above:
            # AutomatonNamespace's automaton.<project> cross-references
            # resolve an OTHER project entirely, a genuinely different
            # mechanism from "the current one's own active project".
            automaton_namespace = AutomatonNamespace(self._db, self._project_service)
            scope_builder = EvaluationScopeBuilder(env, metrics, system, session_facts, user_facts, self._db, automaton_namespace)
            tracking_engine = TrackingEngine(DbTrackingSink(self._db), env, scope_builder)

            scope = scope_builder.build(automaton, state.key, {})
            action = automaton.evaluate_triggers_action(state.key, scope)
            # Self-loop only — re-checked here rather than relying solely on
            # the build-time guarantee, since a wake-up must never apply a
            # real, non-self-loop transition on the user's behalf.
            if action is not None and action.target == state.key:
                tracking_engine.apply_transition(
                    automaton, state, action, {}, session["id"],
                    username=username, project_name=observer_project_name,
                )
                # A "notification" frame, never "done" — chatClient.js drops a
                # "done" with no pendingTurn in flight, which a push never is.
                # Best-effort live nudge only; the transition above is already persisted regardless.
                if self._ws_adapter is not None:
                    await self._ws_adapter.push(username, {
                        "type": "notification",
                        "project_name": observer_project_name,
                        "state": automaton.get_state_payload(state),
                        "on-enter": action.on_enter,
                    })

    def _wake(self, username: str, observer_project_name: str) -> None:
        self._job_queue.submit(WakeupJob(self, username, observer_project_name))
