from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.metrics import Statistics

from .base import _AggregationJob
from .pooled_aggregation_job import PooledAggregationJob
from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class SharedObservationsCache:
    """Coalesces concurrent SignalAggregationJob siblings (see
    AllSignalsAggregationJob) asking for the same run id's observations at
    the same time — a plain dict cache alone doesn't help there: with
    several worker threads racing, every one of them can see a miss and
    rebuild before any of them finishes writing. A per-run-id lock makes
    the first caller build it once while the rest wait for that exact
    result, instead of each redoing the same work in parallel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locks: dict[int, threading.Lock] = {}
        self._data: dict[int, list] = {}

    def get_or_build(self, run_id: int, job: "_AggregationJob") -> list:
        with self._lock:
            run_lock = self._locks.setdefault(run_id, threading.Lock())
        with run_lock:
            cached = self._data.get(run_id)
            if cached is not None:
                return cached
            observations = job._observations_for_run(run_id)
            self._data[run_id] = observations
            return observations


class SignalAggregationJob(_AggregationJob):
    """Unlike its siblings, this one doesn't inherit _AggregationJob's
    single-step _compute() — gathering observations is its slowest part,
    and it scales with session count, so it's spread one run id per step
    (+1 final step to combine and persist) instead of running as one
    opaque step. Each per-run-id step also folds that session's values
    straight into a running Statistics.Accumulator, so the sum/min/max/
    distribution work is spread across those steps too — the final step
    only sorts the retained values for the exact median and persists.
    Gives real incremental progress and lets a worker yield between run
    ids instead of holding the whole aggregation."""

    def __init__(
        self, service: "TestService", project_id: str, signal_name: str, strategy: str, session_ids: list[int],
        observations_cache: SharedObservationsCache | None = None,
    ) -> None:
        super().__init__(service, project_id, 'signal', signal_name, strategy)
        self._signal_name = signal_name
        self._session_ids = session_ids
        self._sessions_job: PooledAggregationJob | None = None
        self._pending_run_ids: list[int] = []
        self._accumulator = Statistics.Accumulator()
        # Shared across every signal's own job by AllSignalsAggregationJob
        # (all resolving the very same run ids) so gathering one run's
        # observations — its slowest part, see the class docstring — happens
        # once total instead of once per signal. A standalone signal click
        # gets its own private, single-use cache, same cost as before.
        self._observations_cache = observations_cache if observations_cache is not None else SharedObservationsCache()

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._sessions_job = self._service._sessions_job(self._project_id, self._strategy)
        return (self._sessions_job,)

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return 1, ()
        dependencies = self._resolve_or_construct_dependencies()
        return len(self._session_ids) + 1, dependencies

    async def _run_next_step(self) -> None:
        if self._result_value is not None:
            return
        if self._sessions_job is not None:
            run_ids_by_session = self._sessions_job.run_ids
            self._pending_run_ids = [run_ids_by_session[sid] for sid in self._session_ids]
            self._sessions_job = None
        if self._pending_run_ids:
            run_id = self._pending_run_ids.pop(0)
            observations = self._observations_cache.get_or_build(run_id, self)
            for observation in observations:
                if self._signal_name in observation.signal_agreements:
                    self._accumulator.add(observation.signal_agreements[self._signal_name])
            return
        result = _serialize_metric_result(self._accumulator.result(self._signal_name, metadata={"unit": "percent"}))
        self._persist(result)
        self._result_value = result
