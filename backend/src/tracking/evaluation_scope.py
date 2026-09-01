"""The one place a trigger/`env:`-expression evaluation scope gets
assembled — every reserved namespace, plus every core metric as a bare
top-level name. Constructed once and shared by every caller that needs
this scope: TrackingEngine's auto-tracking trigger-eval/env-apply paths,
and /api/triggers/preview's live what-if preview off already-known
signal values."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from automaton.automaton import Automaton
from db import Db
from metrics.metric_service import MetricService
from tracking.env import Env
from tracking.evaluator import SignalEvaluator
from tracking.session_facts import SessionFacts
from tracking.sources import SourceNamespace
from tracking.system_facts import SystemFacts
from tracking.user_facts import UserFacts

if TYPE_CHECKING:
    # Import guarded: automaton_namespace -> project_service ->
    # tracking_engine -> this module would close a circular import if
    # done eagerly. Only needed for the annotation below, never at runtime.
    from tracking.automaton_namespace import AutomatonNamespace


class EvaluationScopeBuilder(object):
    def __init__(
        self,
        env: Env,
        metrics: MetricService,
        system: SystemFacts,
        session: SessionFacts,
        user: UserFacts,
        db: Db,
        automaton_namespace: "AutomatonNamespace | None" = None,
    ) -> None:
        self._env = env
        self._metrics = metrics
        self._system = system
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

    def build(self, automaton: Automaton, state_key: str, raw_signal_values: dict[str, Any] | None) -> dict[str, Any]:
        """`raw_signal_values` is always re-coerced against every declared
        signal, never assumed pre-validated. env/system/session/user/source/
        metric are cheap, lazy proxies included unconditionally; only the
        bare core-metric names are gated, since building them is eager.
        `source` is rebuilt fresh every call (unlike env/system/session/
        user, never threaded through __init__) since it needs `automaton`
        itself — a `build()` parameter, not a constructor dependency any
        caller has to wire up separately — to know where source.attachment
        should actually read from (see Automaton.set_storage_location)."""
        signal_values = SignalEvaluator().validate(automaton, raw_signal_values)
        scope: dict[str, Any] = {
            "signal": signal_values,
            "env": self._env.action_set(),
            "system": self._system,
            "session": self._session,
            "user": self._user.as_dict(),
            "source": SourceNamespace(self._db, automaton),
            "metric": self._metrics.for_turn(),
        }
        if self._automaton_namespace is not None:
            scope["automaton"] = self._automaton_namespace
        return self._metrics.merge_if_referenced(automaton, state_key, scope)
