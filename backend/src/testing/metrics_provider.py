"""TestMetricsProvider: MetricsProvider for a test replay run — extracted
out of metrics/metric_service.py since it's a replay-orchestration adapter
(consumed only by test/processor.py), not part of the metrics framework
itself."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from automaton.automaton import Automaton
from db import Db
from metrics.metric_namespace import UserMetricNamespace
from metrics.metric_service import user_scoped_metrics, values_dict
from metrics.metrics_framework import (
    AnalyticsCalculator,
    MetricCalculator,
    MetricResult,
    UserAnalyticsDataBuilder,
)
from metrics.metrics_framework import metric_names as _metric_names


class TestMetricsProvider:
    """MetricsProvider for a test replay, picking its analytical dataset
    per turn (advance_to): a real session uses full cross-session history
    up to real_timestamp; an imported session (no timestamp) scopes to
    just itself, truncated by message id."""

    def __init__(self, db: Db, username: str, project_id: str, session_id: int) -> None:
        self._db = db
        self._username = username
        self._project_id = project_id
        self._session_id = session_id
        # Set once per turn by advance_to; no default bound so a caller
        # that forgets to call it first fails loudly (AttributeError)
        # rather than silently falling back to "no bound at all".

    def advance_to(self, message_id: int, real_timestamp: datetime | None) -> None:
        self._message_id = message_id
        self._real_timestamp = real_timestamp

    def _calculate(self) -> list[tuple[MetricCalculator, MetricResult]]:
        if self._real_timestamp is not None:
            calculator = AnalyticsCalculator(
                self._db, self._username, self._project_id, until=self._real_timestamp
            )
        else:
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_id).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            calculator = AnalyticsCalculator.from_data(data)
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_values(self) -> dict[str, float]:
        return values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace for one replay turn — same laziness as
        MetricService.for_turn, picking its dataset the same per-turn way
        _calculate above does (full history for a real session, truncated otherwise)."""
        def _build_calculator() -> AnalyticsCalculator:
            if self._real_timestamp is not None:
                return AnalyticsCalculator(
                    self._db, self._username, self._project_id,
                    metrics=user_scoped_metrics(), until=self._real_timestamp,
                )
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_id).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            return AnalyticsCalculator.from_data(data, metrics=user_scoped_metrics())
        return UserMetricNamespace(_build_calculator)

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}
