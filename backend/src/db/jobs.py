from __future__ import annotations

from datetime import datetime

from .models import Job
from .utils import _utc_iso


class JobMixin:

    def create_job(self, kind: str, reference_id: int | None, total: int) -> int:
        row = Job.create(kind=kind, reference_id=reference_id, status='pending', progress_total=total)
        return row.id

    def set_job_running(self, job_id: int) -> None:
        Job.update(status='running').where(Job.id == job_id).execute()

    def set_job_progress(self, job_id: int, current: int) -> None:
        Job.update(progress_current=current).where(Job.id == job_id).execute()

    def set_job_completed(self, job_id: int, warning: str | None=None, result: str | None=None) -> None:
        Job.update(
            status='completed', finished_at=datetime.utcnow(), error=warning, result=result,
        ).where(Job.id == job_id).execute()

    def set_job_failed(self, job_id: int, error: str) -> None:
        Job.update(
            status='failed', finished_at=datetime.utcnow(), error=error,
        ).where(Job.id == job_id).execute()

    def get_job(self, job_id: int) -> dict | None:
        row = Job.get_or_none(Job.id == job_id)
        if row is None:
            return None
        return self._job_to_dict(row)

    def get_job_by_reference(self, kind: str, reference_id: int) -> dict | None:
        """The Job tracking one domain row's lifecycle — most recent
        first in the (should be rare) case of more than one."""
        row = (
            Job.select()
            .where((Job.kind == kind) & (Job.reference_id == reference_id))
            .order_by(Job.created_at.desc(), Job.id.desc())
            .first()
        )
        if row is None:
            return None
        return self._job_to_dict(row)

    def delete_jobs_by_reference_ids(self, kind: str, reference_ids: list[int]) -> None:
        if not reference_ids:
            return
        Job.delete().where((Job.kind == kind) & (Job.reference_id.in_(reference_ids))).execute()

    def list_jobs(self, kind: str | None=None) -> list[dict]:
        query = Job.select()
        if kind is not None:
            query = query.where(Job.kind == kind)
        query = query.order_by(Job.created_at.desc(), Job.id.desc())
        return [self._job_to_dict(row) for row in query]

    @staticmethod
    def _job_to_dict(row: Job) -> dict:
        return {
            'id': row.id,
            'kind': row.kind,
            'reference_id': row.reference_id,
            'status': row.status,
            'created_at': _utc_iso(row.created_at),
            'finished_at': _utc_iso(row.finished_at) if row.finished_at is not None else None,
            'error': row.error,
            'result': row.result,
            'progress_current': row.progress_current,
            'progress_total': row.progress_total,
        }
