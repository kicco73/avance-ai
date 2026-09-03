"""An action's on-enter, run as a Task — now or later.

Every `on-enter:` script runs outside the request that fired it: the
transition and its `env:` writes are applied synchronously (they feed
the very next prompt), then the script is hibernated as an OnEnterTask
due *now* and executed by a JobService worker. actuator.prompt is a
model call, actuator.send_mail a network call — neither belongs on the
event-loop thread of a chat turn. What the browser gets (`celebrate()`,
`notify(...)`, `show(...)`) arrives over the websocket as a
"notification" frame instead of inside the turn's own response.

actuator.defer(lambda: <call>, when) is the same task with a later
`when` and the lambda's body as its script — one type, one hydration
path, one table.

What a script "carries" at the moment it is hibernated is the actuator
view of the scope it was evaluated in (see EvaluationScopeBuilder.build
and EvaluationScope.for_actuators):

  frozen at build time     signal (validated values), env (action-set copy,
                           as it was *before* this action's own env: writes,
                           same as the in-turn evaluation always saw), user
                           (User row as dict), bare scalars (core metrics,
                           and names assigned by earlier statements when the
                           script is a deferred lambda)
  live proxies             metric, source, automaton, datetime, actuator —
                           each re-reads Session().user and the project
                           context whenever it is touched
  absent by construction   session — an on-enter line never sees it, so a
                           deferred call can't depend on a session that
                           will long be over when it runs

An equivalent environment later is therefore *not* a pickle of a closure
(its proxies hold a db handle, a project service, a websocket adapter —
and a ContextVar user a worker thread never had): it is the frozen part,
stored verbatim as JSON, plus the live part rebuilt from (username,
project_id, project_revision, state_key) the way tracking/wakeup_service.py
rebuilds a scope for a user who is not the current request —
Session().impersonate(username) and a FixedProjectContext pinned on the
project, at the *revision* the script was written against (published
revisions are kept in Archive; a later republish never reinterprets a
pending script). `session_id` rides along only for an immediate run:
actuator.prompt's own conversation history is that session's, still
open a few hundred milliseconds later; a deferred call has none."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from automaton.automaton import Action, Automaton, DeferredExpression
from automaton.scope import EvaluationScope
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from jobs import CancelableJob, Task
from logging_factory import LoggerFactory
from session import Session

if TYPE_CHECKING:
    from ai import AiService
    from chat.ws_adapter import WsAdapter
    from db import Db
    from project.project_service import ProjectService
    from tracking.actuators.factory import ActuatorSetFactory

logger = LoggerFactory.get_logger(__name__)

# Top-level scope entries stored verbatim. Anything else at the top
# level that is a bare JSON scalar (a core metric merged by
# MetricService.merge_if_referenced, a name assigned by an earlier
# on-enter statement) is frozen too, under "extra".
_FROZEN_NAMESPACES = ("signal", "env", "user")
_SCALARS = (int, float, str, bool, type(None))

ACTUATORS_LIVE = "live"
ACTUATORS_FAKE = "fake"


class OnEnterTask(Task):
    """Pure data (`payload`) plus a hydrator that turns it back into a
    scope at run time. See the module docstring for what the payload
    holds and why."""

    TYPE = "on-enter"

    def __init__(self, key: str, username: str, payload: dict[str, Any], hydrator: "ScopeHydrator") -> None:
        super().__init__(key=key, username=username)
        self._payload = payload
        self._hydrator = hydrator

    # --- construction ----------------------------------------------------

    @classmethod
    def now(
        cls, action: Action, scope: EvaluationScope, *, username: str, actuators: str, session_id: int | None,
        hydrator: "ScopeHydrator",
    ) -> "OnEnterTask":
        """The whole `action.on_enter` script, due immediately."""
        return cls._build(
            action.on_enter or "", scope.for_actuators(action_name=action.name), datetime.now(timezone.utc),
            username=username, actuators=actuators, session_id=session_id, hydrator=hydrator,
        )

    @classmethod
    def later(
        cls, act: DeferredExpression, when: datetime, *, username: str, actuators: str, hydrator: "ScopeHydrator",
    ) -> "OnEnterTask":
        """actuator.defer: the lambda's body as a one-statement script,
        due at `when`, with no session (see module docstring)."""
        return cls._build(
            act.source, act.scope, when, username=username, actuators=actuators, session_id=None, hydrator=hydrator,
        )

    @classmethod
    def _build(
        cls, script: str, scope: EvaluationScope, when: datetime, *, username: str, actuators: str,
        session_id: int | None, hydrator: "ScopeHydrator",
    ) -> "OnEnterTask":
        automaton = scope.automaton
        if automaton.project_id is None:
            raise ValueError("An on-enter task needs a project to be pinned to — the automaton declares no project.id.")
        state = automaton.states.get(scope.state_key)
        payload = {
            "script": script,
            "project_id": automaton.project_id,
            "project_revision": getattr(automaton, "revision", None),
            "state_key": scope.state_key,
            "action_name": scope.action_name,
            "session_id": session_id,
            "actuators": actuators,
            "when": when.isoformat(),
            "snapshot": cls.freeze(scope),
            "ui": {
                "project_label": automaton.project_ui_label or automaton.project_id,
                "state_label": state.ui_label if state is not None else scope.state_key,
            },
        }
        # Fail here, in the request, if anything in the scope is not JSON
        # — never at run time, possibly days later.
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
    def payload(self) -> dict[str, Any]:
        return dict(self._payload)

    @property
    def project_id(self) -> str:
        return self._payload["project_id"]

    @property
    def is_deferred(self) -> bool:
        return self._payload.get("session_id") is None

    @property
    def ui_label(self) -> str:
        ui = self._payload["ui"]
        where = f"{ui['project_label']} · {ui['state_label']}"
        if self._payload.get("action_name"):
            where += f" → {self._payload['action_name']}"
        script = self._payload["script"].strip()
        first_line = script.splitlines()[0] if script else ""
        return f"{where}: {first_line}"

    @property
    def ui_description(self) -> str:
        kind = "Deferred by" if self.is_deferred else "On-enter of"
        return (
            f"{kind} state '{self._payload['state_key']}' on behalf of {self.username}: runs at "
            f"{self._payload['when']} (UTC) against the signals, env and user facts as they were when it was "
            f"scheduled. Script:\n{self._payload['script']}"
        )

    def dehydrate(self) -> dict[str, Any]:
        return self.payload

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        on_enter = self._hydrator.run(self.username, self._payload)
        ws_adapter = self._hydrator.ws_adapter
        if on_enter and ws_adapter is not None:
            await ws_adapter.push(self.username, {"type": "notification", "on-enter": on_enter})


class ScopeHydrator(object):
    """Rebuilds, for a hibernated payload, a scope equivalent to the one
    its script was evaluated in, and runs the script in it. The factory
    is held rather than its products so that the websocket adapter
    (bound late, see main.py) and the actuator set — which lets a script
    itself defer again, persisting the chain — are always resolved at
    run time, never captured."""

    def __init__(
        self, db: "Db", project_service: "ProjectService", actuator_factory: "ActuatorSetFactory",
        ai_service: "AiService | None",
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._actuator_factory = actuator_factory
        self._ai_service = ai_service

    @property
    def ws_adapter(self) -> "WsAdapter | None":
        return self._actuator_factory.ws_adapter

    def hydrate(self, key: str, username: str, payload: dict[str, Any]) -> OnEnterTask:
        """JobService's hydrator for OnEnterTask.TYPE. Cheap and
        side-effect free: the project is only resolved when the task
        actually runs."""
        for field in ("script", "project_id", "project_revision", "state_key", "snapshot", "actuators"):
            if field not in payload:
                raise ValueError(f"Task {key} payload is missing '{field}'.")
        return OnEnterTask(key, username, payload, self)

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
        if payload["actuators"] == ACTUATORS_FAKE:
            actuator_set = self._actuator_factory.fake(project_id=project_id)
        else:
            actuator_set = self._actuator_factory.live(project_id=project_id)
        builder = EvaluationScopeBuilder(
            PersistedEnv(self._db, context), MetricService(self._db, context),
            SessionFacts(self._db, context), UserFacts(self._db), self._db,
            AutomatonNamespace(self._db, self._project_service), actuator_set,
            ai_service=self._ai_service,
        )
        # actuator.prompt's conversation history: the firing session's,
        # for an immediate run — only while that session still exists.
        session_id = payload.get("session_id")
        if session_id is not None and self._db.get_chat_session(session_id) is None:
            session_id = None
        snapshot = payload["snapshot"]
        scope = builder.build(automaton, payload["state_key"], snapshot.get("signal") or {}, session_id=session_id)
        # The frozen part wins over whatever the live proxies would say
        # now — exactly what the in-turn evaluation would have seen.
        for name in _FROZEN_NAMESPACES:
            scope[name] = snapshot.get(name, {})
        scope.update(snapshot.get("extra", {}))
        return scope.for_actuators(action_name=payload.get("action_name"))

    def run(self, username: str, payload: dict[str, Any]) -> str | None:
        with Session().impersonate(username):
            scope = self.build_scope(username, payload)
            return Automaton.render_on_enter_script(payload["script"], scope)
