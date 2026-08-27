from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse

from .job import Job
from .job_queue import JobQueue

if TYPE_CHECKING:
    from testing.queue_progress_broadcaster import QueueProgressBroadcaster


def stream_job_progress(
    job_queue: JobQueue, broadcaster: "QueueProgressBroadcaster", job: Job
) -> StreamingResponse:
    """Submits `job` (already carrying its own key/username, see Job.__init__)
    and streams its progress back as SSE on this same response — one
    connection per request, closed the moment the job completes or fails.
    Shared by every "run a job, watch it inline" endpoint (session import,
    project upload)."""
    connection = broadcaster.connect(job.username)
    job_queue.submit(job)

    async def stream():
        try:
            while True:
                message = await connection.get()
                if message["status"] in ("completed", "failed"):
                    if message["status"] == "completed" and job.result:
                        message = {**message, "result": json.loads(job.result)}
                    yield f"data: {json.dumps(message)}\n\n"
                    return
                yield f"data: {json.dumps(message)}\n\n"
        finally:
            broadcaster.disconnect(job.username, connection)

    return StreamingResponse(stream(), media_type="text/event-stream")
