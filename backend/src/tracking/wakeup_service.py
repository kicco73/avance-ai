"""Cross-project wake-up (Prompt 6) — when a project's own state/env
changes for some user (see events.events.StateChanged/EnvChanged,
published by tracking.tracking_engine.TrackingEngine), every OTHER
project that references it via automaton.* (the reverse index, see
db.observability.ObservabilityMixin/project.project_service.
ProjectService's own _finalize_project_update) and that same user has
ever talked to (even a dormant session — "ever talked to" is exactly
what a session's own existence means here, live or not) gets a chance
to re-evaluate its own triggers, in case one of its own self-loop
actions (the only kind ever allowed to reference automaton.*, see
automaton_builder.py's own build-time check) now fires.

One handler, registered for both event types (see register()) — neither
carries anything the other doesn't already have (event.username,
event.project_name), so there's nothing to special-case between them.
"""
from __future__ import annotations

import logging

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
    def __init__(self, db: Db, project_service: ProjectService, ephemeral_jobs: JobQueue) -> None:
        self._db = db
        self._project_service = project_service
        self._ephemeral_jobs = ephemeral_jobs

    def register(self) -> None:
        subscribe(StateChanged, self._on_event)
        subscribe(EnvChanged, self._on_event)

    def _on_event(self, event: StateChanged | EnvChanged) -> None:
        # Never lets a wake-up failure propagate back into publish()'s
        # own caller (see events.dispatcher's own docstring: a handler
        # that raises propagates straight out) — that caller is always
        # some *other* project's real turn (TrackingEngine.
        # notify_transition/apply_action_env), which must complete
        # regardless of whether waking up an observer succeeds.
        try:
            for observer_project_name in self._db.get_observers(event.project_name):
                if self._db.get_latest_chat_session(event.username, observer_project_name) is not None:
                    self._wake(event.username, observer_project_name)
        except Exception:
            logger.exception(
                "Wake-up dispatch failed for %s in project '%s'.", type(event).__name__, event.project_name
            )

    async def _reevaluate_and_apply(self, username: str, observer_project_name: str) -> None:
        """The actual re-evaluation (see _wake's own docstring for why
        this is a separate method): re-derives `observer_project_name`'s
        own current scope from scratch — a fresh Env/MetricService/
        SessionFacts/AutomatonNamespace bound to this (username,
        observer_project_name) pair, mirroring exactly what
        ChatService.__init__/TrackingService.process build for a real
        turn's own active project (see either one's own construction) —
        then re-evaluates its triggers and applies a self-loop
        transition if (and only if) one fires."""
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
        # Self-loop only — never anything else, even if some other,
        # unrelated action happened to be the one whose trigger fired
        # first (see evaluate_triggers_action's own FIFO docstring):
        # automaton_builder.py's own build-time check already guarantees
        # no *automaton.*-referencing* action can be anything but a
        # self-loop, but this re-checks regardless rather than relying
        # on that alone — a wake-up must never apply a real, non-
        # self-loop transition on the user's behalf, outside of any turn
        # they actually took.
        if action is not None and action.target == state.key:
            tracking_engine.apply_transition(
                automaton, state, action, {}, session["id"],
                username=username, project_name=observer_project_name,
            )

    def _wake(self, username: str, observer_project_name: str) -> None:
        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            await self._reevaluate_and_apply(username, observer_project_name)
            return None, None

        self._ephemeral_jobs.submit(kind="automaton_wakeup", reference_id=None, total=1, work=work)
