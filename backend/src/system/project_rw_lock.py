from __future__ import annotations

import asyncio


class ProjectRwLock:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_readers = 0
        self._waiting_writers = 0
        self._writer_active = False

    async def acquire_read(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer_active and self._waiting_writers == 0)
            self._active_readers += 1

    async def release_read(self) -> None:
        async with self._condition:
            self._active_readers -= 1
            if self._active_readers == 0:
                self._condition.notify_all()

    async def acquire_write(self) -> None:
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(lambda: not self._writer_active and self._active_readers == 0)
                self._writer_active = True
            finally:
                self._waiting_writers -= 1

    async def release_write(self) -> None:
        async with self._condition:
            self._writer_active = False
            self._condition.notify_all()
