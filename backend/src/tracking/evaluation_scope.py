"""The one place a trigger/`env:`-expression evaluation scope gets
assembled — every reserved namespace (see automaton.automaton.
RESERVED_NAMESPACES, plus `session`'s own `metric` sub-namespace) plus
every core metric as a bare top-level name (unchanged, outside this
component's own concern — see metrics.metric_service.MetricService/
MetricsProvider). Constructed once (see chat.chat_service.ChatService)
and shared by every caller that needs this scope: tracking.
tracking_engine.TrackingEngine (auto-tracking's own trigger-eval/env-apply
paths) and controller.py's own /api/triggers/preview (a live what-if
preview of the same trigger evaluation, off already-known signal values
instead of ones freshly computed this turn).
"""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton
from metrics.metric_service import MetricService
from tracking.env import Env
from tracking.evaluator import SignalEvaluator
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts


class EvaluationScopeBuilder(object):
    def __init__(self, env: Env, metrics: MetricService, system: SystemFacts, session: SessionFacts) -> None:
        self._env = env
        self._metrics = metrics
        self._system = system
        self._session = session

    def build(self, automaton: Automaton, state_key: str, raw_signal_values: dict[str, Any] | None) -> dict[str, Any]:
        """`raw_signal_values`: whatever's actually known this turn (an
        AI-reported/explicitly-computed dict, or {} for a manual action —
        see chat_service.py's apply_manual_action) — always re-coerced
        against every declared signal here (see SignalEvaluator.
        validate), never assumed pre-validated by the caller.

        `env`/`system`/`session`/`metric` are always included,
        unconditionally: env.action_set() is a plain cheap read, and
        system/session/metric are all zero-arg-callable proxies (see
        their own modules) that only do any real work if an expression
        actually calls one of their methods — so there's nothing left to
        gain from the skip-if-unreferenced check env/system/session used
        to need (see tracking.env.Env's own docstring: it used to compute
        all of this itself). `session.metric.for_turn()`/self._metrics.
        for_turn() are the same story: cheap to call every turn, since
        the AnalyticsCalculator each one is backed by is itself only
        built lazily, on first actual use (see metrics.metric_namespace.
        LazyMetricNamespace). The bare, unnamespaced core-metric names
        (see this module's own docstring) are the one exception still
        gated behind MetricsProvider.merge_if_referenced, since
        *that* mechanism does build its AnalyticsCalculator eagerly —
        worth skipping whenever nothing in this state's own triggers
        could reference one at all."""
        signal_values = SignalEvaluator().validate(automaton, raw_signal_values)
        scope: dict[str, Any] = {
            "signal": signal_values,
            "env": self._env.action_set(),
            "system": self._system,
            "session": self._session,
            "metric": self._metrics.for_turn(),
        }
        return self._metrics.merge_if_referenced(automaton, state_key, scope)
