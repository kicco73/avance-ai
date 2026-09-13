from __future__ import annotations

import threading
from contextlib import contextmanager

from db import Db
from jobs import CancelableJob, Job


class TestCache:

    def __init__(self, db: Db) -> None:
        self._db = db
        self._lock = threading.RLock()
        self._live_jobs: dict[int, Job] = {}

    @contextmanager
    def locked(self):
        with self._lock:
            yield

    def find(
        self, session_id: int | None, strategy: str, project_draft_edit_count: int, session_labeling_revision: int | None,
    ) -> dict | None:
        # FIXME: caller must hold locked().
        if session_id is None:
            return None
        run = self._db.find_test_by_cache_key(
            session_id, strategy, project_draft_edit_count, session_labeling_revision,
        )
        if run is None:
            return None
        job = self._live_jobs.get(run['id'])
        dead = job is None or job.is_failed() or (isinstance(job, CancelableJob) and job.is_aborted())
        if run['results'] is None and dead:
            self._live_jobs.pop(run['id'], None)
            return None
        return run

    def create(
        self, username: str | None, project_id: str, session_id: int | None, strategy: str,
        project_draft_edit_count: int, session_labeling_revision: int | None, ai_model_snapshot: dict,
    ) -> dict:
        # FIXME: caller must hold locked().
        return self._db.create_test(
            username, project_id, session_id, strategy,
            project_draft_edit_count, session_labeling_revision, ai_model_snapshot,
        )

    def track(self, run_id: int, job: Job) -> None:
        # FIXME: caller must hold locked(), same critical section as
        self._live_jobs[run_id] = job

    def live_job_for(self, run_id: int) -> Job | None:
        with self._lock:
            return self._live_jobs.get(run_id)

    def untrack_many(self, run_ids: list[int]) -> None:
        with self._lock:
            for run_id in run_ids:
                self._live_jobs.pop(run_id, None)
