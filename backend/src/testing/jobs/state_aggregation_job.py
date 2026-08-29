from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric

from .base import _AggregationJob
from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class StateAggregationJob(_AggregationJob):

    def __init__(self, service: "TestService", project_name: str, state_key: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'state', state_key, strategy)
        self._state_key = state_key
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> dict:
        observations = self._observations_for(self._sub_run_ids)
        filtered = tuple(o for o in observations if o.expected_state == self._state_key)
        return _serialize_metric_result(SignalAccuracyMetric().calculate(filtered))
