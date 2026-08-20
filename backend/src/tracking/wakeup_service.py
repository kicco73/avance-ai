"""Cross-project wake-up: when a project's state/env changes for a user,
every OTHER project referencing it via automaton.* that the same user has
ever talked to gets a chance to re-evaluate its triggers. One handler
serves both event types — neither carries anything the other doesn't."""
from __future__ import annotations

import logging

from automaton.automaton import Automaton
from chat.ws_adapter import WsAdapter
from db.db import Db
from events import EnvChanged, StateChanged, subscribe
from jobs import JobQueue, OnProgress
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine

logger = logging.getLogger(__name__)


class WakeupService:
    def __init__(
        self, db: Db, project_service: ProjectService, ephemeral_jobs: JobQueue, ws_adapter: WsAdapter | None = None,
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._ephemeral_jobs = ephemeral_jobs
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
            for observer_project_name in self._db.get_observers(event.project_name):
                if self._db.get_latest_chat_session(event.username, observer_project_name) is not None:
                    self._wake(event.username, observer_project_name)
        except Exception:
            logger.exception(
                "Wake-up dispatch failed for %s in project '%s'.", type(event).__name__, event.project_name
            )

    async def _reevaluate_and_apply(self, username: str, observer_project_name: str) -> None:
        """Re-derives `observer_project_name`'s own current scope from
        scratch — a fresh Env/MetricService/SessionFacts/AutomatonNamespace
        bound to this (username, observer_project_name) pair — then applies a self-loop transition if one fires."""
        session = self._db.get_latest_chat_session(username, observer_project_name)
        if session is None:
            return  # deleted between dispatch and this job actually running

        automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])

        get_username = lambda: username
        get_project_name = lambda: observer_project_name
        env = PersistedEnv(self._db, get_username=get_username, get_active_project_name=get_project_name)
        metrics = MetricService(self._db, get_username=get_username, get_active_project_name=get_project_name)
        system = SystemFacts()
        session_facts = SessionFacts(self._db, get_username=get_username, get_active_project_name=get_project_name)
        automaton_namespace = AutomatonNamespace(self._db, self._project_service, get_username)
        scope_builder = EvaluationScopeBuilder(env, metrics, system, session_facts, automaton_namespace)
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
                    "state": Automaton.get_state_payload(state),
                    "on-enter": action.on_enter,
                })

    def _wake(self, username: str, observer_project_name: str) -> None:
        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            await self._reevaluate_and_apply(username, observer_project_name)
            return None, None

        self._ephemeral_jobs.submit(kind="automaton_wakeup", reference_id=None, total=1, work=work)
