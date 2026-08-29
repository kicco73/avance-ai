from __future__ import annotations

from statistics import mean, median, pstdev
from typing import TYPE_CHECKING

from jobs import CancelableJob

from .base import _AggregationJob
from .pooled_aggregation_job import PooledAggregationJob
from .serialization import _job_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class UsersAggregationJob(_AggregationJob):
    """Depends on one PooledAggregationJob('user_sessions', ...) per user
    — not raw session ids directly — so each user's own "user:*" tree
    node gets its own real progress, the same way clicking that user's
    play button standalone (start_user_sessions_run_job) would."""

    def __init__(
        self, service: "TestService", project_name: str, strategy: str, session_ids_by_user: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'users', None, strategy)
        self._session_ids_by_user = session_ids_by_user
        self._user_jobs: list[PooledAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._user_jobs = [
            self._service._track(
                PooledAggregationJob(self._service, self._project_name, 'user_sessions', username, self._strategy, session_ids)
            )
            for username, session_ids in self._session_ids_by_user.items()
        ]
        return tuple(self._user_jobs)

    async def _compute(self) -> list[dict]:
        per_user_results = [_job_result(job) for job in self._user_jobs]
        return self._aggregate_across_results(per_user_results)

    def _aggregate_across_results(self, per_group_results: list[list[dict]]) -> list[dict]:
        results_by_name: dict[str, list[dict]] = {}
        for results in per_group_results:
            for result in results:
                results_by_name.setdefault(result['name'], []).append(result)

        aggregated = []
        for name, results in results_by_name.items():
            total_sample_count = sum(result['sample_count'] for result in results)
            with_samples = [result for result in results if result['sample_count']]
            values = [result['value'] for result in with_samples]
            if not values:
                aggregated.append({
                    'name': name, 'value': 0.0, 'mean': None, 'median': None,
                    'standard_deviation': None, 'minimum': None, 'maximum': None,
                    'sample_count': total_sample_count, 'distribution': self._merge_distributions(results),
                    'components': {},
                })
                continue
            aggregated.append({
                'name': name,
                'value': mean(values),
                'mean': mean(values),
                'median': median(values),
                'standard_deviation': pstdev(values) if len(values) > 1 else 0.0,
                'minimum': min(values),
                'maximum': max(values),
                'sample_count': total_sample_count,
                'distribution': self._merge_distributions(results),
                'components': with_samples[0]['components'] if len(with_samples) == 1 else {},
            })
        return aggregated
