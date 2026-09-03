from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob

from .base import _AggregationJob
from .serialization import _job_result
from .signal_aggregation_job import SharedObservationsCache, SignalAggregationJob

if TYPE_CHECKING:
    from testing.test_service import TestService


class AllSignalsAggregationJob(_AggregationJob):
    """Depends on one SignalAggregationJob per signal — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "TestService", project_id: str, strategy: str, session_ids: list[int], signal_names: list[str],
    ) -> None:
        super().__init__(service, project_id, 'all_signals', None, strategy)
        self._session_ids = session_ids
        self._signal_names = signal_names
        self._signal_jobs: list[SignalAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        observations_cache = SharedObservationsCache()
        self._signal_jobs = [
            self._service._track(SignalAggregationJob(
                self._service, self._project_id, signal_name, self._strategy, self._session_ids,
                observations_cache=observations_cache,
            ))
            for signal_name in self._signal_names
        ]
        return tuple(self._signal_jobs)

    async def _compute(self) -> dict:
        per_signal_results = [_job_result(job) for job in self._signal_jobs]
        return self._aggregate_weighted_by_sample_count(per_signal_results)
