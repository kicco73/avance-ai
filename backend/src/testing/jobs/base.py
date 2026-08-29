from __future__ import annotations

import json
from typing import TYPE_CHECKING

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.dto import BenchmarkConfiguration
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkObservationBuilder
from session import Session
from testing.data import TestDataBuilder

if TYPE_CHECKING:
    from testing.test_service import TestService


_NODE_ID_BY_KIND = {
    'sessions': 'sessions-branch',
    'users': 'users-branch',
    'all_states': 'states-branch',
    'all_signals': 'signals-branch',
}


def _aggregation_node_id(kind: str, target: str | None) -> str:
    if kind in _NODE_ID_BY_KIND:
        return _NODE_ID_BY_KIND[kind]
    if kind == 'user_sessions':
        return f'user:{target}'
    return f'{kind}:{target}'


class _AggregationJob(CancelableJob):
    """Common shape for every aggregation job kind: check the cache first,
    compute, persist, return."""

    def __init__(self, service: "TestService", project_name: str, kind: str, target: str | None, strategy: str) -> None:
        super().__init__(key=f"{strategy}:{_aggregation_node_id(kind, target)}", username=Session().user)
        self._service = service
        self._project_name = project_name
        self._kind = kind
        self._target = target
        self._strategy = strategy
        self._result_value: dict | list[dict] | None = None

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        # Checked here, before any dependency is even resolved — not just
        # in _run_next_step() — so an already-cached node needs no
        # dependencies at all: none of its underlying sessions' (possibly
        # expensive, real-AI-call) TestReplayJobs get constructed or
        # re-run just to re-derive an answer this node already has cached.
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return 1, ()
        return 1, self._resolve_or_construct_dependencies()

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        raise NotImplementedError

    @property
    def session_ids(self) -> list[int]:
        return self._session_ids

    @property
    def run_ids(self) -> dict[int, int]:
        return dict(zip(self._session_ids, self._sub_run_ids))

    def _resolve_session_ids(self, session_ids: list[int]) -> tuple[list[int], tuple[CancelableJob, ...]]:
        run_ids = []
        dependencies = []
        for session_id in session_ids:
            run_id, job = self._resolve_or_construct_session_run(session_id)
            run_ids.append(run_id)
            if job is not None:
                dependencies.append(job)
        return run_ids, tuple(dependencies)

    def _resolve_or_construct_session_run(self, session_id: int) -> tuple[int, CancelableJob | None]:
        # Whole method under the cache lock — otherwise two concurrent
        # callers can both see "nothing running yet", both fall through to
        # _construct_run, and the loser gets back job=None (since the row
        # already exists by the time it gets there) instead of the winner's
        # live job, silently dropping the dependency on an in-flight run.
        with self._service._cache.locked():
            candidates = [
                run for run in self._service.list_runs(self._project_name, session_id) if run['strategy'] == self._strategy
            ]
            candidate = candidates[0] if candidates and not candidates[0]['stale'] else None
            if candidate is not None:
                # 'running' — some other branch of the same root click already
                # claimed this exact session — must still be depended on here
                # too, not silently treated as "nothing to wait for" just
                # because a (still in-flight) row already exists.
                if candidate['status'] == 'running':
                    live_job = self._service._cache.live_job_for(candidate['id'])
                    assert live_job is not None
                    return candidate['id'], live_job
                if candidate['status'] == 'completed':
                    return candidate['id'], None
                # 'failed' or 'aborted' — a dead attempt; fall through to retry below.
            session = self._service._db.get_chat_session(session_id)
            assert session is not None
            run, job = self._service._construct_run(session['username'], self._project_name, session_id, self._strategy)
            return run['id'], job

    def _observations_for_run(self, run_id: int) -> list:
        run = self._service._db.get_test(run_id)
        if run is None:
            return []
        data = TestDataBuilder.build(self._service._db, run)
        return BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data)

    def _observations_for(self, run_ids: list[int]) -> list:
        observations: list = []
        for run_id in run_ids:
            observations.extend(self._observations_for_run(run_id))
        return observations

    @staticmethod
    def _merge_distributions(results: list[dict]) -> list[int]:
        """Element-wise sum of each result's own histogram — the correct
        way to roll several already-binned distributions (e.g. one per
        sub-group) into the single combined one a branch/root node shows,
        without ever needing the raw per-observation values again."""
        bucket_count = max((len(result.get('distribution') or []) for result in results), default=0)
        if not bucket_count:
            return []
        merged = [0] * bucket_count
        for result in results:
            for i, count in enumerate(result.get('distribution') or []):
                merged[i] += count
        return merged

    def _aggregate_weighted_by_sample_count(self, results: list[dict]) -> dict:
        total_sample_count = sum(result['sample_count'] for result in results)
        with_samples = [result for result in results if result['sample_count']]
        if not with_samples:
            return {
                'name': 'overall', 'value': 0.0, 'mean': None, 'median': None,
                'standard_deviation': None, 'minimum': None, 'maximum': None,
                'sample_count': total_sample_count, 'distribution': self._merge_distributions(results),
                'components': {},
            }
        weighted_value = sum(
            result['value'] * result['sample_count'] for result in with_samples
        ) / total_sample_count
        return {
            'name': 'overall',
            'value': weighted_value,
            'mean': None, 'median': None, 'standard_deviation': None, 'minimum': None, 'maximum': None,
            'sample_count': total_sample_count,
            'distribution': self._merge_distributions(results),
            'components': {},
        }

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps(self._result_value) if self._result_value is not None else None

    def _cached(self) -> dict | list[dict] | None:
        edit_count = self._service._db.get_project_draft_edit_count(self._project_name)
        return self._service._db.find_test_aggregate_result(
            self._project_name, self._kind, self._target, self._strategy, edit_count,
        )

    def _persist(self, result: dict | list[dict]) -> None:
        revision = self._service._db.get_project_revision(self._project_name)
        edit_count = self._service._db.get_project_draft_edit_count(self._project_name)
        self._service._db.upsert_test_aggregate_result(
            self._project_name, revision, edit_count, self._kind, self._target, self._strategy, json.dumps(result),
        )

    async def _compute(self) -> dict | list[dict]:
        raise NotImplementedError

    async def _run_next_step(self) -> None:
        # _prepare() already resolved this from cache when possible (see
        # above) — self._result_value is only still None here when it
        # genuinely had to wait on real dependencies.
        if self._result_value is not None:
            return
        result = await self._compute()
        self._persist(result)
        self._result_value = result
