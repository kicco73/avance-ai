from __future__ import annotations

import asyncio

import pytest

from metrics.queue_progress_broadcaster import QueueProgressBroadcaster

pytestmark = pytest.mark.contract


class _FakeAiServiceForTokens:
    def __init__(self, total: int = 0) -> None:
        self.total = total
        self.calls = 0

    def get_total_tokens(self) -> int:
        self.calls += 1
        return self.total


async def test_push_enriches_the_message_with_the_current_token_total():
    ai_service = _FakeAiServiceForTokens(total=42)
    broadcaster = QueueProgressBroadcaster(ai_service, batch_window_seconds=0.01)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"key": "batch:session:1", "status": "running"})

    message = await asyncio.wait_for(connection.get(), timeout=1.0)
    assert message == {"key": "batch:session:1", "status": "running", "tokens": 42}


async def test_push_does_not_deliver_instantly():
    ai_service = _FakeAiServiceForTokens(total=1)
    broadcaster = QueueProgressBroadcaster(ai_service, batch_window_seconds=0.20)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"status": "running"})

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(connection.get(), timeout=0.05)
    await asyncio.wait_for(connection.get(), timeout=1.0)


async def test_pushes_within_the_batch_window_are_merged_into_one_message():
    ai_service = _FakeAiServiceForTokens(total=1)
    broadcaster = QueueProgressBroadcaster(ai_service, batch_window_seconds=0.05)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"key": "job:1", "status": "running"})
    ai_service.total = 7
    broadcaster.push("user", {"key": "job:1", "status": "completed"})

    message = await asyncio.wait_for(connection.get(), timeout=1.0)
    assert message == {"key": "job:1", "status": "completed", "tokens": 7}
    assert ai_service.calls == 1
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(connection.get(), timeout=0.1)


async def test_concurrent_jobs_do_not_clobber_each_others_updates():
    ai_service = _FakeAiServiceForTokens(total=3)
    broadcaster = QueueProgressBroadcaster(ai_service, batch_window_seconds=0.05)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"key": "batch:session:1", "status": "running"})
    broadcaster.push("user", {"key": "batch:session:2", "status": "running"})
    broadcaster.push("user", {"key": "batch:session:1", "status": "completed"})

    first = await asyncio.wait_for(connection.get(), timeout=1.0)
    second = await asyncio.wait_for(connection.get(), timeout=1.0)
    delivered = {first["key"]: first, second["key"]: second}
    assert delivered["batch:session:1"]["status"] == "completed"
    assert delivered["batch:session:2"]["status"] == "running"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(connection.get(), timeout=0.1)


async def test_push_skips_the_ai_service_call_when_nobody_is_connected():
    ai_service = _FakeAiServiceForTokens(total=99)
    broadcaster = QueueProgressBroadcaster(ai_service, batch_window_seconds=0.01)

    broadcaster.push("nobody-connected", {"status": "running"})

    await asyncio.sleep(0.05)
    assert ai_service.calls == 0
