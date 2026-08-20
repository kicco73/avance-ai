from __future__ import annotations

import threading
from datetime import datetime
from typing import Protocol

from db import Db, _utc_iso


class JobSink(Protocol):
    """Whatever JobQueue needs to create/track a job's lifecycle —
    orchestration only, no domain data. Both implementations must be
    thread-safe: every method here is called from a worker thread."""

    def create(self, kind: str, reference_id: int | None, total: int) -> int:
        ...

    def set_running(self, job_id: int) -> None:
        ...

    def set_progress(self, job_id: int, current: int) -> None:
        ...

    def set_completed(self, job_id: int, warning: str | None=None, result: str | None=None) -> None:
        ...

    def set_failed(self, job_id: int, error: str) -> None:
        ...

    def get(self, job_id: int) -> dict | None:
        ...

    def list(self, kind: str | None=None) -> list[dict]:
        ...


class PersistedJobSink:
    """JobSink backed by the real Db — production's own sink for a job
    kind that should still be listed/inspectable after the process that
    ran it is gone."""

    def __init__(self, db: Db) -> None:
        self._db = db

    def create(self, kind: str, reference_id: int | None, total: int) -> int:
        return self._db.create_job(kind, reference_id, total)

    def set_running(self, job_id: int) -> None:
        self._db.set_job_running(job_id)

    def set_progress(self, job_id: int, current: int) -> None:
        self._db.set_job_progress(job_id, current)

    def set_completed(self, job_id: int, warning: str | None=None, result: str | None=None) -> None:
        self._db.set_job_completed(job_id, warning=warning, result=result)

    def set_failed(self, job_id: int, error: str) -> None:
        self._db.set_job_failed(job_id, error)

    def get(self, job_id: int) -> dict | None:
        return self._db.get_job(job_id)

    def list(self, kind: str | None=None) -> list[dict]:
        return self._db.list_jobs(kind)


class InMemoryJobSink:
    """JobSink for a deliberately ephemeral job kind — nothing survives a
    process restart. A threading.Lock, not asyncio.Lock: this is
    read/written from worker threads, each with its own event loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[int, dict] = {}
        self._next_id = 1

    def create(self, kind: str, reference_id: int | None, total: int) -> int:
        with self._lock:
            job_id = self._next_id
            self._next_id += 1
            self._jobs[job_id] = {
                'id': job_id,
                'kind': kind,
                'reference_id': reference_id,
                'status': 'pending',
                'created_at': _utc_iso(datetime.utcnow()),
                'finished_at': None,
                'error': None,
                'result': None,
                'progress_current': 0,
                'progress_total': total,
            }
            return job_id

    def set_running(self, job_id: int) -> None:
        with self._lock:
            self._jobs[job_id]['status'] = 'running'

    def set_progress(self, job_id: int, current: int) -> None:
        with self._lock:
            self._jobs[job_id]['progress_current'] = current

    def set_completed(self, job_id: int, warning: str | None=None, result: str | None=None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job['status'] = 'completed'
            job['finished_at'] = _utc_iso(datetime.utcnow())
            job['error'] = warning
            job['result'] = result

    def set_failed(self, job_id: int, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job['status'] = 'failed'
            job['finished_at'] = _utc_iso(datetime.utcnow())
            job['error'] = error

    def get(self, job_id: int) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def list(self, kind: str | None=None) -> list[dict]:
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values() if kind is None or job['kind'] == kind]
        return sorted(jobs, key=lambda job: job['id'], reverse=True)
