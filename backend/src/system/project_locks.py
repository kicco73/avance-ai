from __future__ import annotations

from contextlib import asynccontextmanager

from system.keyed_lock_registry import KeyedLockRegistry
from system.project_rw_lock import ProjectRwLock


class ProjectLocks:
    def __init__(self) -> None:
        self._locks = KeyedLockRegistry(ProjectRwLock)

    @asynccontextmanager
    async def acquire_read(self, project_id: str):
        lock = self._locks.get(project_id)
        await lock.acquire_read()
        try:
            yield
        finally:
            await lock.release_read()

    @asynccontextmanager
    async def acquire_write(self, project_id: str):
        lock = self._locks.get(project_id)
        await lock.acquire_write()
        try:
            yield
        finally:
            await lock.release_write()

    async def drain(self, project_id: str) -> None:
        async with self.acquire_write(project_id):
            pass
