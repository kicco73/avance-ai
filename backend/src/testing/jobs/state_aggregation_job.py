from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric

from .base import _AggregationJob
from .pooled_aggregation_job import PooledAggregationJob
from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class StateAggregationJob(_AggregationJob):

    def __init__(self, service: "TestService", project_name: str, state_key: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'state', state_key, strategy)
        self._state_key = state_key
        self._session_ids = session_ids
        self._sessions_job: PooledAggregationJob | None = None

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._sessions_job = self._service._sessions_job(self._project_name, self._strategy)
        return (self._sessions_job,)

    async def _compute(self) -> dict:
        run_ids_by_session = self._sessions_job.run_ids
        sub_run_ids = [run_ids_by_session[sid] for sid in self._session_ids]
        observations = self._observations_for(sub_run_ids)
        filtered = tuple(o for o in observations if o.expected_state == self._state_key)
        return _serialize_metric_result(SignalAccuracyMetric().calculate(filtered))
