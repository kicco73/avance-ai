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
    broadcaster = QueueProgressBroadcaster(ai_service)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"key": "batch:session:1", "status": "running"})

    message = await asyncio.wait_for(connection.get(), timeout=1.0)
    assert message == {"key": "batch:session:1", "status": "running", "tokens": 42}


async def test_push_reflects_the_latest_total_on_every_call():
    ai_service = _FakeAiServiceForTokens(total=1)
    broadcaster = QueueProgressBroadcaster(ai_service)
    connection = broadcaster.connect("user")

    broadcaster.push("user", {"status": "running"})
    ai_service.total = 7
    broadcaster.push("user", {"status": "completed"})

    first = await asyncio.wait_for(connection.get(), timeout=1.0)
    second = await asyncio.wait_for(connection.get(), timeout=1.0)
    assert first["tokens"] == 1
    assert second["tokens"] == 7


async def test_push_skips_the_ai_service_call_when_nobody_is_connected():
    ai_service = _FakeAiServiceForTokens(total=99)
    broadcaster = QueueProgressBroadcaster(ai_service)

    broadcaster.push("nobody-connected", {"status": "running"})

    assert ai_service.calls == 0
