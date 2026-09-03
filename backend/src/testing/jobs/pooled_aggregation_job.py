from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkData
from testing.data import TestDataBuilder

from .base import _AggregationJob
from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class PooledAggregationJob(_AggregationJob):

    def __init__(
        self, service: "TestService", project_id: str, kind: str, target: str | None, strategy: str,
        session_ids: list[int],
    ) -> None:
        super().__init__(service, project_id, kind, target, strategy)
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []
        self._sessions_job: PooledAggregationJob | None = None

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        if self._kind == 'sessions':
            self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
            return dependencies
        self._sessions_job = self._service._sessions_job(self._project_id, self._strategy)
        return (self._sessions_job,)

    async def _compute(self) -> list[dict]:
        if self._sessions_job is not None:
            run_ids_by_session = self._sessions_job.run_ids
            self._sub_run_ids = [run_ids_by_session[sid] for sid in self._session_ids]
        if not self._sub_run_ids:
            return []
        db = self._service._db
        runs = [run for run_id in self._sub_run_ids if (run := db.get_test(run_id)) is not None]
        frames = [TestDataBuilder.build(db, run) for run in runs]
        pooled = BenchmarkData(
            messages=pd.concat([f.messages for f in frames], ignore_index=True),
            sessions=pd.concat([f.sessions for f in frames], ignore_index=True),
            signals=pd.concat([f.signals for f in frames], ignore_index=True),
            transitions=pd.concat([f.transitions for f in frames], ignore_index=True),
        )
        unfiltered_metrics = BenchmarkCalculator(db, None, self._project_id).default_metrics()
        calculator = BenchmarkCalculator.from_data(pooled, metrics=unfiltered_metrics)
        return [_serialize_metric_result(result) for result in calculator.calculate_all()]
