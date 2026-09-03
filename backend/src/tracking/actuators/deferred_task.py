"""actuator.defer(lambda: <call>, when) — the Task it becomes, and the
hydrator that gives it back its environment when it is due.

What a deferred lambda "carries" at the moment defer() is called, in
the closure the evaluator hands us, is the actuator view of the
on-enter scope it was evaluated in (see EvaluationScopeBuilder.build
and EvaluationScope.for_actuators):

  frozen at build time     signal (validated values), env (action-set copy),
                           user (User row as dict), bare core-metric numbers
  live proxies             metric, source, automaton, datetime, actuator —
                           each re-reads Session().user and the project
                           context whenever it is touched
  absent by construction   session — an on-enter line never sees it, so a
                           deferred call can't depend on a session that
                           will long be over when it runs

So an equivalent environment after a restart is *not* a pickle of that
closure (its proxies hold a db handle, a project service, a websocket
adapter — and a ContextVar user that a worker thread never had anyway):
it is the frozen part, stored verbatim as JSON, plus the live part,
rebuilt from (username, project_id, project_revision, state_key) the
way tracking/wakeup_service.py already rebuilds a scope for a user who
is not the current request — Session().impersonate(username) and a
FixedProjectContext pinned on the project. The automaton is reloaded
at the *revision* the call was deferred from (published revisions are
kept in Archive; a later republish never reinterprets a pending call).
The lambda body itself is stored as source text (ast.unparse) and
re-evaluated through the same _OnEnterEval grammar that produced it.

The task takes that path on *every* run, restart or not — a freshly
deferred call never executes the captured closure, so the hibernation
path is exercised continuously rather than only after a crash."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from automaton.automaton import _OnEnterEval, DeferredExpression
from automaton.scope import EvaluationScope
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from jobs import CancelableJob, Task
from logging_factory import LoggerFactory
from session import Session

if TYPE_CHECKING:
    from chat.ws_adapter import WsAdapter
    from db import Db
    from project.project_service import ProjectService
    from tracking.actuators.factory import ActuatorSetFactory

logger = LoggerFactory.get_logger(__name__)

# Top-level scope entries stored verbatim. Anything else at the top
# level that is a bare JSON scalar (a core metric merged by
# MetricService.merge_if_referenced) is frozen too, under "extra".
_FROZEN_NAMESPACES = ("signal", "env", "user")
_SCALARS = (int, float, str, bool, type(None))


class DeferredActuatorTask(Task):
    """Pure data (`payload`) plus a hydrator that turns it back into a
    scope at run time. See the module docstring for what the payload
    holds and why."""

    TYPE = "actuator-defer"

    def __init__(self, key: str, username: str, payload: dict[str, Any], hydrator: "ScopeHydrator") -> None:
        super().__init__(key=key, username=username)
        self._payload = payload
        self._hydrator = hydrator

    @classmethod
    def from_expression(
        cls, act: DeferredExpression, when: datetime, *, username: str, hydrator: "ScopeHydrator",
    ) -> "DeferredActuatorTask":
        scope = act.scope
        automaton = scope.automaton
        if automaton.project_id is None:
            raise ValueError("A deferred call needs a project to be pinned to — the automaton declares no project.id.")
        state = automaton.states.get(scope.state_key)
        payload = {
            "expression": act.source,
            "project_id": automaton.project_id,
            "project_revision": getattr(automaton, "revision", None),
            "state_key": scope.state_key,
            "action_name": scope.action_name,
            "when": when.isoformat(),
            "snapshot": cls.freeze(scope),
            "ui": {
                "project_label": automaton.project_ui_label or automaton.project_id,
                "state_label": state.ui_label if state is not None else scope.state_key,
            },
        }
        # Fail at defer time, in the request, if anything in the scope
        # is not JSON — never at boot, days later.
        json.dumps(payload)
        return cls(f"{cls.TYPE}:{uuid.uuid4()}", username, payload, hydrator)

    @staticmethod
    def freeze(scope: dict[str, Any]) -> dict[str, Any]:
        frozen: dict[str, Any] = {name: scope.get(name, {}) for name in _FROZEN_NAMESPACES}
        frozen["extra"] = {
            name: value for name, value in scope.items()
            if name not in TriggerExpressionAnalyzer.RESERVED_NAMESPACES and isinstance(value, _SCALARS)
        }
        return frozen

    # --- Task ------------------------------------------------------------

    @property
    def project_id(self) -> str:
        return self._payload["project_id"]

    @property
    def ui_label(self) -> str:
        ui = self._payload["ui"]
        where = f"{ui['project_label']} · {ui['state_label']}"
        if self._payload.get("action_name"):
            where += f" → {self._payload['action_name']}"
        return f"{where}: {self._payload['expression']}"

    @property
    def ui_description(self) -> str:
        return (
            f"Deferred by state '{self._payload['state_key']}' on behalf of {self.username}: "
            f"runs `{self._payload['expression']}` at {self._payload['when']} (UTC) against the signals, "
            f"env and user facts as they were when it was deferred."
        )

    def dehydrate(self) -> dict[str, Any]:
        return dict(self._payload)

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        on_enter = self._hydrator.evaluate(self.username, self._payload)
        ws_adapter = self._hydrator.ws_adapter
        if on_enter and ws_adapter is not None:
            await ws_adapter.push(self.username, {"type": "notification", "on-enter": on_enter})


class ScopeHydrator(object):
    """Rebuilds, for a hibernated payload, a scope equivalent to the one
    its lambda was evaluated in, and evaluates the expression in it. The
    factory is held rather than its products so that the websocket
    adapter (bound late, see main.py) and the live actuator set — which
    lets a deferred call itself defer again, persisting the chain — are
    always resolved at run time, never captured."""

    def __init__(self, db: "Db", project_service: "ProjectService", actuator_factory: "ActuatorSetFactory") -> None:
        self._db = db
        self._project_service = project_service
        self._actuator_factory = actuator_factory

    @property
    def ws_adapter(self) -> "WsAdapter | None":
        return self._actuator_factory.ws_adapter

    def hydrate(self, key: str, username: str, payload: dict[str, Any]) -> DeferredActuatorTask:
        """JobService's hydrator for DeferredActuatorTask.TYPE. Cheap and
        side-effect free: the project is only resolved when the task
        actually runs."""
        for field in ("expression", "project_id", "project_revision", "state_key", "snapshot"):
            if field not in payload:
                raise ValueError(f"Task {key} payload is missing '{field}'.")
        return DeferredActuatorTask(key, username, payload, self)

    def build_scope(self, username: str, payload: dict[str, Any]) -> EvaluationScope:
        """Must be called under Session().impersonate(username): every
        live proxy below reads Session().user lazily."""
        # Imported here, not at module level: tracking.evaluation_scope ->
        # this package -> these modules -> project_service -> tracking_engine
        # -> tracking.evaluation_scope would otherwise close a circular import.
        from metrics.metric_service import MetricService
        from tracking.automaton_namespace import AutomatonNamespace
        from tracking.env import PersistedEnv
        from tracking.evaluation_scope import EvaluationScopeBuilder
        from tracking.fixed_project_context import FixedProjectContext
        from tracking.session_facts import SessionFacts
        from tracking.user_facts import UserFacts

        project_id: str = payload["project_id"]
        automaton = self._project_service.get_automaton(project_id, payload["project_revision"])
        context = FixedProjectContext(automaton=automaton, project_id=project_id)
        builder = EvaluationScopeBuilder(
            PersistedEnv(self._db, context), MetricService(self._db, context),
            SessionFacts(self._db, context), UserFacts(self._db), self._db,
            AutomatonNamespace(self._db, self._project_service),
            self._actuator_factory.live(project_id=project_id),
        )
        snapshot = payload["snapshot"]
        scope = builder.build(automaton, payload["state_key"], snapshot.get("signal") or {})
        # The frozen part wins over whatever the live proxies would say
        # now — exactly what the closure would have seen.
        for name in _FROZEN_NAMESPACES:
            scope[name] = snapshot.get(name, {})
        scope.update(snapshot.get("extra", {}))
        return scope.for_actuators(action_name=payload.get("action_name"))

    def evaluate(self, username: str, payload: dict[str, Any]) -> str | None:
        with Session().impersonate(username):
            scope = self.build_scope(username, payload)
            result = _OnEnterEval(names=scope).eval(payload["expression"])
        return result if isinstance(result, str) else None
