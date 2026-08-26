from __future__ import annotations

import json

from peewee import fn

from .models import BenchmarkRun, BenchmarkRunObservation
from .utils import _utc_iso

# Distinguishes "no username filter at all" (this sentinel, the default)
# from an explicitly passed `username=None` — which instead filters down
# to the multi-user pool runs (BenchmarkRun.username IS NULL).
_USERNAME_UNSPECIFIED = object()


class BenchmarkRunMixin:

    def create_benchmark_run(
        self, username: str | None, project_name: str, session_id: int | None, strategy: str,
        project_draft_edit_count: int, session_labeling_revision: int | None, ai_model_snapshot: dict,
    ) -> dict:
        # A prior dead/failed attempt (no results, no live job — see
        # BenchmarkRunCache.find) can still occupy this exact cache key;
        # discard it first so the retry's own insert doesn't collide with
        # it under the same unique index.
        BenchmarkRun.delete().where(
            (BenchmarkRun.session == session_id) & (BenchmarkRun.strategy == strategy)
            & (BenchmarkRun.project_draft_edit_count == project_draft_edit_count)
            & (BenchmarkRun.session_labeling_revision == session_labeling_revision)
        ).execute()
        row = BenchmarkRun.create(
            username=username, project_name=project_name, session=session_id, strategy=strategy,
            project_draft_edit_count=project_draft_edit_count, session_labeling_revision=session_labeling_revision,
            ai_model_snapshot=json.dumps(ai_model_snapshot),
        )
        return self._benchmark_run_to_dict(row)

    def find_benchmark_run_by_cache_key(
        self, session_id: int, strategy: str, project_draft_edit_count: int, session_labeling_revision: int,
    ) -> dict | None:
        row = BenchmarkRun.get_or_none(
            (BenchmarkRun.session == session_id) & (BenchmarkRun.strategy == strategy)
            & (BenchmarkRun.project_draft_edit_count == project_draft_edit_count)
            & (BenchmarkRun.session_labeling_revision == session_labeling_revision)
        )
        return self._benchmark_run_to_dict(row) if row is not None else None

    def get_benchmark_run(self, run_id: int) -> dict | None:
        row = BenchmarkRun.get_or_none(BenchmarkRun.id == run_id)
        if row is None:
            return None
        return self._benchmark_run_to_dict(row)

    def list_benchmark_runs(
        self, project_name: str, session_id: int | None=None, username: str | None=_USERNAME_UNSPECIFIED,
    ) -> list[dict]:
        query = BenchmarkRun.select().where(BenchmarkRun.project_name == project_name)
        if session_id is None:
            query = query.where(BenchmarkRun.session.is_null(True))
        else:
            query = query.where(BenchmarkRun.session == session_id)
        if username is not _USERNAME_UNSPECIFIED:
            if username is None:
                query = query.where(BenchmarkRun.username.is_null(True))
            else:
                query = query.where(BenchmarkRun.username == username)
        return [self._benchmark_run_to_dict(row) for row in query]

    def delete_benchmark_runs(self, project_name: str) -> list[int]:
        run_ids = [
            row.id for row in BenchmarkRun.select(BenchmarkRun.id).where(BenchmarkRun.project_name == project_name)
        ]
        if run_ids:
            BenchmarkRun.delete().where(BenchmarkRun.id.in_(run_ids)).execute()
        return run_ids

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
            'project_draft_edit_count': row.project_draft_edit_count,
            'session_labeling_revision': row.session_labeling_revision,
            'batch_segments': row.batch_segments,
            'ai_model_snapshot': json.loads(row.ai_model_snapshot) if row.ai_model_snapshot else None,
            'results': json.loads(row.results) if row.results else None,
        }
