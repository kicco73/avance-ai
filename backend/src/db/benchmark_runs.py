from __future__ import annotations

import json

from peewee import fn

from .models import BenchmarkRun, BenchmarkRunObservation
from .utils import _utc_iso


class BenchmarkRunMixin:

    def create_benchmark_run(
        self, username: str, project_name: str, session_id: int | None, strategy: str,
        project_revision: int, ai_model_snapshot: dict,
    ) -> dict:
        row = BenchmarkRun.create(
            username=username, project_name=project_name, session=session_id, strategy=strategy,
            project_revision=project_revision, ai_model_snapshot=json.dumps(ai_model_snapshot),
        )
        return self._benchmark_run_to_dict(row)

    def get_benchmark_run(self, run_id: int) -> dict | None:
        row = BenchmarkRun.get_or_none(BenchmarkRun.id == run_id)
        if row is None:
            return None
        return self._benchmark_run_to_dict(row)

    def list_benchmark_runs(self, project_name: str, session_id: int | None=None) -> list[dict]:
        query = BenchmarkRun.select().where(BenchmarkRun.project_name == project_name)
        if session_id is None:
            query = query.where(BenchmarkRun.session.is_null(True))
        else:
            query = query.where(BenchmarkRun.session == session_id)
        return [self._benchmark_run_to_dict(row) for row in query]

    def set_benchmark_run_results(self, run_id: int, results: str) -> None:
        BenchmarkRun.update(results=results).where(BenchmarkRun.id == run_id).execute()

    def add_benchmark_run_batch_segments(self, run_id: int, segments: int) -> None:
        """Atomic accumulate-across-sessions increment — a batch run's
        `work` calls this once per session, never overwriting what a
        previous session in the same run already added."""
        BenchmarkRun.update(
            batch_segments=fn.COALESCE(BenchmarkRun.batch_segments, 0) + segments
        ).where(BenchmarkRun.id == run_id).execute()

    def get_benchmark_run_observations(self, run_id: int, session_ids: list[int] | None=None) -> list[dict]:
        query = BenchmarkRunObservation.select().where(BenchmarkRunObservation.run == run_id)
        if session_ids is not None:
            query = query.where(BenchmarkRunObservation.session.in_(session_ids))
        query = query.order_by(BenchmarkRunObservation.session, BenchmarkRunObservation.id)
        return [{
            'id': row.id,
            'message_id': row.message_id,
            'timestamp': _utc_iso(row.timestamp),
            'values': row.values,
            'old_state': row.old_state,
            'action': row.action,
            'new_state': row.new_state,
            'session_id': row.session_id,
        } for row in query]

    @staticmethod
    def _benchmark_run_to_dict(row: BenchmarkRun) -> dict:
        return {
            'id': row.id,
            'username': row.username,
            'project_name': row.project_name,
            'session_id': row.session_id,
            'strategy': row.strategy,
            'project_revision': row.project_revision,
            'batch_segments': row.batch_segments,
            'ai_model_snapshot': json.loads(row.ai_model_snapshot) if row.ai_model_snapshot else None,
            'results': json.loads(row.results) if row.results else None,
        }
