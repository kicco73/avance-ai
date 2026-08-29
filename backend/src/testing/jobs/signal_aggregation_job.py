from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.metrics import Statistics

from .base import _AggregationJob
from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


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

    def __init__(self, service: "TestService", project_name: str, signal_name: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'signal', signal_name, strategy)
        self._signal_name = signal_name
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []
        self._pending_run_ids: list[int] = []
        self._accumulator = Statistics.Accumulator()

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return 1, ()
        dependencies = self._resolve_or_construct_dependencies()
        self._pending_run_ids = list(self._sub_run_ids)
        return len(self._pending_run_ids) + 1, dependencies

    async def _run_next_step(self) -> None:
        if self._result_value is not None:
            return
        if self._pending_run_ids:
            run_id = self._pending_run_ids.pop(0)
            for observation in self._observations_for_run(run_id):
                if self._signal_name in observation.signal_agreements:
                    self._accumulator.add(observation.signal_agreements[self._signal_name])
            return
        result = _serialize_metric_result(self._accumulator.result(self._signal_name, metadata={"unit": "percent"}))
        self._persist(result)
        self._result_value = result
