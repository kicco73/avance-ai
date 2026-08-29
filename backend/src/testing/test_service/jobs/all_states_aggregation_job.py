from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob

from .base import _AggregationJob
from .serialization import _job_result
from .state_aggregation_job import StateAggregationJob

if TYPE_CHECKING:
    from testing.test_service import TestService


class AllStatesAggregationJob(_AggregationJob):
    """Depends on one StateAggregationJob per state — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "TestService", project_name: str, strategy: str, session_ids_by_state: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'all_states', None, strategy)
        self._session_ids_by_state = session_ids_by_state
        self._state_jobs: list[StateAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._state_jobs = [
            StateAggregationJob(self._service, self._project_name, state_key, self._strategy, session_ids)
            for state_key, session_ids in self._session_ids_by_state.items()
        ]
        return tuple(self._state_jobs)

    async def _compute(self) -> dict:
        per_state_results = [_job_result(job) for job in self._state_jobs]
        return self._aggregate_weighted_by_sample_count(per_state_results)
