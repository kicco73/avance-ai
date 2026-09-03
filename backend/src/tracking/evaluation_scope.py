"""The one place a trigger/`env:`-expression evaluation scope gets
assembled — every reserved namespace, plus every core metric as a bare
top-level name. Constructed once and shared by every caller that needs
this scope: TrackingEngine's auto-tracking trigger-eval/env-apply paths,
and /api/triggers/preview's live what-if preview off already-known
signal values."""
from __future__ import annotations

import datetime
from typing import Any, TYPE_CHECKING

from simpleeval import ModuleWrapper

from automaton.automaton import Automaton
from automaton.scope import EvaluationScope
from db import Db
from metrics.metric_service import MetricService
from tracking.actuators import ActuatorSet, FakeActuatorSet
from tracking.env import Env
from tracking.evaluator import SignalEvaluator
from tracking.session_facts import SessionFacts
from tracking.sources import SourceNamespace
from tracking.user_facts import UserFacts

if TYPE_CHECKING:
    # Import guarded: automaton_namespace -> project_service ->
    # tracking_engine -> this module would close a circular import if
    # done eagerly. Only needed for the annotation below, never at runtime.
    from tracking.automaton_namespace import AutomatonNamespace
    # Import guarded for the same reason: ai.ai_service is a heavy,
    # unrelated dependency graph — only needed for the annotation below.
    from ai import AiService


class EvaluationScopeBuilder(object):
    def __init__(
        self,
        env: Env,
        metrics: MetricService,
        session: SessionFacts,
        user: UserFacts,
        db: Db,
        automaton_namespace: "AutomatonNamespace | None" = None,
        actuator_set: ActuatorSet | None = None,
        ai_service: "AiService | None" = None,
    ) -> None:
        self._env = env
        self._metrics = metrics
        self._session = session
        self._user = user
        # Only for SourceNamespace (source.attachment(name) reads
        # straight from storage — see tracking.sources.attachment) —
        # every other namespace above wraps its own db access already.
        self._db = db
        # Optional — a test replay omits it, so its scope has no
        # "automaton" namespace: an automaton.* reference there fails to
        # resolve rather than doing real cross-project work during a replay.
        self._automaton_namespace = automaton_namespace
        self._actuator_set = actuator_set if actuator_set is not None else FakeActuatorSet()
        # Optional — actuator.prompt() only actually runs a generation
        # call once this is given; every caller with no real AiService
        # (test replay, /api/triggers/preview) omits it, so actuator.prompt()
        # there just returns "" rather than doing real work.
        self._ai_service = ai_service

    def build(
        self, automaton: Automaton, state_key: str, raw_signal_values: dict[str, Any] | None,
    ) -> EvaluationScope:
        """`raw_signal_values` is always re-coerced against every declared
        signal, never assumed pre-validated. env/session/user/source/
        metric are cheap, lazy proxies included unconditionally; only the
        bare core-metric names are gated, since building them is eager.
        `source` is rebuilt fresh every call (unlike env/session/
        user, never threaded through __init__) since it needs `automaton`
        itself — a `build()` parameter, not a constructor dependency any
        caller has to wire up separately — to know where source.attachment
        should actually read from (see Automaton.set_storage_location)."""
        signal_values = SignalEvaluator().validate(automaton, raw_signal_values)
        scope: dict[str, Any] = {
            "signal": signal_values,
            "env": self._env.action_set(),
            "session": self._session,
            "user": self._user.as_dict(),
            "source": SourceNamespace(self._db, automaton),
            "metric": self._metrics.for_turn(),
            # FIXME: simpleeval rejects a raw module ("modules are not allowed") — ModuleWrapper is its
            # sanctioned opt-in; don't replace this with the bare `datetime` module.
            "datetime": ModuleWrapper(datetime, allowed_attrs={"datetime", "timedelta", "timezone"}),
        }
        if self._automaton_namespace is not None:
            scope["automaton"] = self._automaton_namespace.scoped_to(automaton.family)
        if self._ai_service is not None:
            scope["actuator"] = self._actuator_set.with_ai_service(self._ai_service)
        else:
            scope["actuator"] = self._actuator_set
        merged = self._metrics.merge_if_referenced(automaton, state_key, scope)
        return EvaluationScope(merged, automaton=automaton, state_key=state_key)
