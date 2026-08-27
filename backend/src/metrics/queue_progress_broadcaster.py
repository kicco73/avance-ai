"""Compatibility re-export: the real QueueProgressBroadcaster now lives in
test/queue_progress_broadcaster.py. Kept here only because jobs/job_queue.py
(marked "DO NOT TOUCH THIS FILE") still imports from this path under
TYPE_CHECKING."""
from __future__ import annotations

from testing.queue_progress_broadcaster import QueueProgressBroadcaster as QueueProgressBroadcaster
