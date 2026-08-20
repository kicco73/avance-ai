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
        # None whenever config.chat_transport isn't 'websocket' at all
        # (see main.py's own chat_ws_adapter) — push (see _reevaluate_and_
        # apply below) is simply skipped in that case, same as it always
        # implicitly was before this parameter existed: a re-evaluated
        # self-loop still gets applied and persisted either way, only the
        # live delivery to an already-open connection is what depends on
        # this.
        self._ws_adapter = ws_adapter

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
            # Delivers the transition to a client that's actually
            # connected right now but not mid-turn on *this* project (see
            # WsAdapter.push's own docstring). Never "done" (Prompt 13 —
            # correction to Prompt 12's own reuse of that type): that's
            # the frame type a normal turn's own response uses, and
            # chatClient.js drops any "done" with no pendingTurn actually
            # in flight, which a push never is — a push is never the
            # answer to something the client itself asked for, so it
            # needs its own "notification" type, plus project_name (no
            # longer implied by the lookup key itself, see WsAdapter's own
            # username-only registry) so the client can tell whether this
            # is about the project it's currently showing.
            # None whenever there's no websocket transport configured at
            # all (see __init__), or (push's own return) nobody's
            # actually connected right now — either way, the transition
            # above is already persisted regardless; this is purely a
            # best-effort live nudge.
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
