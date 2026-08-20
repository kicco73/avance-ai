from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Awaitable, Callable

from .job_sink import JobSink

logger = logging.getLogger(__name__)

# work() gets a sync progress callback (safe to call from the async body
# — it runs on this job's own dedicated thread) and resolves to
# (warning, result), carried verbatim to sink.set_completed.
OnProgress = Callable[[int], None]
JobWork = Callable[[OnProgress], Awaitable[tuple[str | None, str | None]]]


class JobQueue:
    """Generic async-job orchestrator: create, queue, run on a dedicated
    thread pool (each with its own event loop, isolated from the main
    one). A job must never await another job on this *same* queue — every worker could deadlock waiting on itself."""

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
