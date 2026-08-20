"""MetricService: everything concerning a project's metrics lives here —
both metrics.metrics_framework's core, always-on metrics (engagement,
retention, ...) and its expert-annotation-vs-actual benchmark metrics.
Architecturally analogous to AiService/ChatService/TrackingService
(instantiated once in main.py, constructor-injected everywhere it's
needed — see main.py's own wiring) rather than something another service
builds for itself. A leaf service: depends only on `db` and metrics.
metrics_framework, never on ChatService or TrackingService — TrackingService
(via its own AutoTracker, for trigger-evaluation's merge_if_referenced)
and ChatService (for the Inspector Metrics tab / benchmark Performance
tab / an action's own `env:` field eval scope) both consume this
directly; it never consumes either of them back, so the dependency only
ever points one way.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol

from automaton.automaton import Automaton
from chat.session_manager import DEFAULT_OPEN_WINDOW_MINUTES
from db import Db
from metrics.metric_namespace import UserMetricNamespace
from metrics.metrics_framework import (
    AnalyticsCalculator,
    BenchmarkCalculator,
    BenchmarkConfiguration,
    MetricCalculator,
    MetricResult,
    UserAnalyticsDataBuilder,
)
from metrics.metrics_framework import metric_names as _metric_names

# Every default metric whose own scope declares "all_sessions_per_user"
# but not "one_session" — the `metric` namespace's own membership (see
# MetricService.for_turn/UserMetricNamespace). Excluding "one_session"
# (rather than just checking "all_sessions_per_user" in scope) matters:
# BaseMetric's own default scope is *every* MetricScope (see BaseMetric.
# scope), so a plain "all_sessions_per_user" in scope check alone would
# also match Engagement/StateStability/SignalStability — those belong to
# `session.metric` instead (see tracking.session_facts.SessionFacts.metric),
# never both.
def _user_scoped_metrics() -> list[MetricCalculator]:
    return [
        metric for metric in AnalyticsCalculator.default_metrics()
        if "all_sessions_per_user" in metric.scope and "one_session" not in metric.scope
    ]

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]
GetMaxSessionDurationInMinutes = Callable[[], float]


class MetricsProvider(Protocol):
    """Whatever a caller needs from a metrics source to evaluate
    triggers/action env — MetricService satisfies this today purely by
    having the same two methods with the same signatures (structural,
    duck-typed — no explicit inheritance needed); a benchmark-replay
    equivalent (not introduced here) will satisfy it too, without either
    one depending on the other."""

    def calculate_values(self) -> dict[str, float]:
        ...

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        ...


def _values_dict(pairs: list[tuple[MetricCalculator, MetricResult]]) -> dict[str, float]:
    """Flat {name: value}, omitting any metric whose result.value is
    None (see BaseMetric.unavailable) — a trigger referencing an
    omitted name simply evaluates False (Automaton._eval_trigger), like
    any other signal never estimated. Shared by MetricService.
    calculate_values and BenchmarkMetricsProvider.calculate_values, so
    the filtering itself is never duplicated between them."""
    return {metric.name: result.value for metric, result in pairs if result.value is not None}


class MetricService(object):
    def __init__(
        self,
        db: Db,
        get_username: GetUsername,
        get_active_project_name: GetActiveProjectName,
        # Same single source of truth ChatSessionManager's own open-
        # session window uses (config.yml's chat-service.
        # max_session_duration_in_minutes, see main.py) — reused here
        # rather than duplicating the "60.0" default independently, and
        # never taken from a live ChatSessionManager instance itself:
        # this service has no other reason to depend on chat/ at all.
        get_max_session_duration_in_minutes: GetMaxSessionDurationInMinutes = lambda: DEFAULT_OPEN_WINDOW_MINUTES,
    ) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name
        self._get_max_session_duration_in_minutes = get_max_session_duration_in_minutes

    def _calculate(
        self, until: datetime | None = None, project_name: str | None = None
    ) -> list[tuple[MetricCalculator, MetricResult]]:
        """One AnalyticsCalculator per call: loads the analytical dataset
        once, then evaluates every core metric against it — the one place
        that construction happens, shared by calculate_all/calculate_values
        so neither duplicates it. `until` restricts the dataset to what
        existed at or before that point (see AnalyticsCalculator).
        `project_name` omitted falls back to the bound get_active_project_
        name callable (see __init__) — calculate_values/for_turn/
        merge_if_referenced (the live turn-evaluation callers) always omit
        it; calculate_all's own explicit-project_name callers (see its own
        docstring) don't."""
        calculator = AnalyticsCalculator(
            self._db, self._get_username(), project_name or self._get_active_project_name(), until=until
        )
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_all(self, until: datetime | None = None, project_name: str | None = None) -> list[dict]:
        """ui_label/ui_description/value per metric, for the "Edit
        project" view's Inspector Metrics tab (live, `until` and
        `project_name` both omitted — always the active project) and the
        "Label sessions" view's point-in-time Inspector (`until` set to a
        specific past message's timestamp, `project_name` its own
        props.projectName explicitly — see ChatService.get_metrics — so
        reviewing project A's session never silently reports whatever
        project B happens to be globally active right now)."""
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
            }
            for metric, result in self._calculate(until=until, project_name=project_name)
        ]

    def calculate_values(self) -> dict[str, float]:
        """Flat {name: value} — for merging into trigger-evaluation's
        `names` dict alongside signal values (see merge_if_referenced),
        and into an action's own `env:` field eval scope (see
        tracking/tracking_engine.py's own apply_action_env). A metric
        with no meaningful value in the current context (result.value is
        None — see BaseMetric.unavailable) is omitted entirely, exactly
        like a signal that was never estimated: a trigger referencing it
        simply evaluates False (Automaton._eval_trigger), never crashes."""
        return _values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace's own value for one turn's evaluation
        scope (see tracking.evaluation_scope.EvaluationScopeBuilder) —
        cheap to call unconditionally every turn, same as SystemFacts/
        SessionFacts: no AnalyticsCalculator is actually built (no DB
        query) until an expression calls one of the returned namespace's
        own methods, and once one does, the *same* calculator instance
        (until=now, no since — the user's whole cross-session history)
        backs every metric referenced off it for the rest of this turn.
        Does not touch calculate_values/calculate_all above — those stay
        exactly as they were for their own existing callers."""
        return UserMetricNamespace(lambda: AnalyticsCalculator(
            self._db, self._get_username(), self._get_active_project_name(),
            metrics=_user_scoped_metrics(), until=datetime.utcnow(),
        ))

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` (signal values headed for trigger evaluation — see
        automaton.py's Automaton.evaluate_triggers/preview_triggers),
        augmented with core metric values — but only when at least one
        triggerable action leaving `state_key` actually references a
        metric name (see Automaton.triggers_reference). Computing metrics
        means loading this user+project's whole message/session/signal
        history (see AnalyticsCalculator) — worth skipping whenever a
        project's triggers never mention one at all, which is the common
        case for most turns/projects."""
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}

    def get_benchmark_metrics(self, session_id: int | None = None, project_name: str | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics (see metrics.
        metrics_framework.benchmark_metrics) for `project_name` (omitted:
        the active project) — every annotated session, or (session_id
        given) just that one. Same {name, ui_label, ui_description, value}
        shape as calculate_all, plus `sample_count` (how many annotated
        points fed each metric — see the framework's own README on why
        that must never be discarded alongside the score) — the "Label
        sessions" view's Performance tab. Ownership of `session_id`, when
        given, is the caller's own responsibility (see ChatService.
        get_benchmark_metrics)."""
        configuration = BenchmarkConfiguration(
            max_session_duration_in_minutes=self._get_max_session_duration_in_minutes()
        )
        calculator = BenchmarkCalculator(
            self._db, self._get_username(), project_name or self._get_active_project_name(),
            configuration=configuration, session_id=session_id,
        )
        results = calculator.calculate_all()
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
                "sample_count": result.sample_count,
            }
            for metric, result in zip(calculator.metrics, results)
        ]


class BenchmarkMetricsProvider:
    """MetricsProvider (see above) for a benchmark replay (not
    introduced here) — same calculate_values/merge_if_referenced shape
    as MetricService, just choosing its own analytical dataset per turn
    (advance_to) instead of always the full live cross-session history.

    Two data sources, picked per-turn by whether a real timestamp is
    available (see advance_to): a real (non-imported) session still has
    its own genuine chronology, so metrics can be computed the exact
    same way production would at that instant (full cross-session
    history, `until=real_timestamp`); an imported session (or any turn
    with no real timestamp to anchor to) instead scopes to just the one
    session being replayed, truncated by message id (see
    UserAnalyticsDataBuilder.build_for_session) — an id is always
    available and meaningful, a real elapsed-time comparison isn't."""

    def __init__(self, db: Db, username: str, project_name: str, session_id: int) -> None:
        self._db = db
        self._username = username
        self._project_name = project_name
        self._session_id = session_id
        # Set once per turn by advance_to, before calculate_values/
        # merge_if_referenced are ever called for that turn — no
        # default bound: a replay orchestrator that forgets to call it
        # first should fail loudly (AttributeError), not silently fall
        # back to "no bound at all".

    def advance_to(self, message_id: int, real_timestamp: datetime | None) -> None:
        self._message_id = message_id
        self._real_timestamp = real_timestamp

    def _calculate(self) -> list[tuple[MetricCalculator, MetricResult]]:
        if self._real_timestamp is not None:
            calculator = AnalyticsCalculator(
                self._db, self._username, self._project_name, until=self._real_timestamp
            )
        else:
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_name).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            calculator = AnalyticsCalculator.from_data(data)
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_values(self) -> dict[str, float]:
        return _values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace's own value for one replay turn (see
        tracking.evaluation_scope.EvaluationScopeBuilder, which every
        BenchmarkProcessor turn builds one of — see metrics/
        benchmark_processor.py) — same laziness as MetricService.for_turn
        (no AnalyticsCalculator built, no DB query, until an expression
        actually calls one of the returned namespace's own methods), just
        picking its dataset the same per-turn way _calculate above
        already does (full cross-session history `until=real_timestamp`
        for a real session, this one session truncated by message id
        otherwise) instead of always "now, everything so far"."""
        def _build_calculator() -> AnalyticsCalculator:
            if self._real_timestamp is not None:
                return AnalyticsCalculator(
                    self._db, self._username, self._project_name,
                    metrics=_user_scoped_metrics(), until=self._real_timestamp,
                )
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_name).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            return AnalyticsCalculator.from_data(data, metrics=_user_scoped_metrics())
        return UserMetricNamespace(_build_calculator)

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}
