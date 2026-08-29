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
        self, service: "TestService", project_name: str, kind: str, target: str | None, strategy: str,
        session_ids: list[int],
    ) -> None:
        super().__init__(service, project_name, kind, target, strategy)
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> tuple[CancelableJob, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> list[dict]:
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
        unfiltered_metrics = BenchmarkCalculator(db, None, self._project_name).default_metrics()
        calculator = BenchmarkCalculator.from_data(pooled, metrics=unfiltered_metrics)
        return [_serialize_metric_result(result) for result in calculator.calculate_all()]
