from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Awaitable, Callable

from .job_sink import JobSink

logger = logging.getLogger(__name__)

# work() gets a sync progress callback (safe to call from inside the async
# body — it's running on this job's own dedicated thread, never the main
# asyncio loop) and resolves to (warning, result): both optional, carried
# through verbatim to sink.set_completed on success.
OnProgress = Callable[[int], None]
JobWork = Callable[[OnProgress], Awaitable[tuple[str | None, str | None]]]


class JobQueue:
    """Generic async-job orchestrator: create, queue, run on a dedicated
    thread, track status/progress/error — nothing about what a job
    actually does. A fixed-size pool of daemon worker threads pulls from
    a plain queue.Queue (not asyncio.Queue — submit() is called from the
    main asyncio loop's thread, but work must run isolated from it, see
    below) with no capacity cap: a job submitted while every worker is
    busy just waits its turn, it is never rejected.

    Each worker thread owns one asyncio event loop for its entire
    lifetime, reused across every job it picks up — this is what lets a
    job's own `work` stay written with async/await (it typically awaits
    an AI call) while still running fully isolated from the app's main
    event loop. That isolation matters because db.py (peewee) is
    synchronous and blocking: a job with many writes running directly on
    the main loop would freeze every other concurrent request for the
    job's whole duration, not just for an instant.

    A job must never await another job submitted to this *same* queue —
    it would occupy one of this pool's threads while waiting for a thread
    that may never free up (every thread could end up in that same
    blocked state), a structural deadlock. Waiting on a job from a
    *different* queue (e.g. a persisted queue's job) is fine."""

    def __init__(self, sink: JobSink, max_concurrent: int) -> None:
        self._sink = sink
        self._queue: queue.Queue[tuple[int, JobWork]] = queue.Queue()
        for i in range(max_concurrent):
            thread = threading.Thread(target=self._worker_loop, name=f"job-worker-{i}", daemon=True)
            thread.start()

    def submit(self, kind: str, reference_id: int | None, total: int, work: JobWork) -> int:
        job_id = self._sink.create(kind, reference_id, total)
        self._queue.put((job_id, work))
        return job_id

    def get(self, job_id: int) -> dict | None:
        """Read-only passthrough to this queue's own sink — for a caller
        that only knows "which queue", never the sink implementation
        behind it (see BenchmarkRunService.get_job_status)."""
        return self._sink.get(job_id)

    def _worker_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            job_id, work = self._queue.get()
            self._sink.set_running(job_id)
            try:
                on_progress: OnProgress = lambda current, _job_id=job_id: self._sink.set_progress(_job_id, current)
                warning, result = loop.run_until_complete(work(on_progress))
                self._sink.set_completed(job_id, warning=warning, result=result)
            except Exception as exc:
                logger.exception("Job %s failed.", job_id)
                self._sink.set_failed(job_id, str(exc))
