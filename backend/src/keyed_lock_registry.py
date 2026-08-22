from __future__ import annotations

import threading
from typing import Any, Callable
from weakref import WeakValueDictionary


class KeyedLockRegistry:
    def __init__(self, lock_factory: Callable[[], Any]) -> None:
        self._locks: WeakValueDictionary[str, Any] = WeakValueDictionary()
        self._guard = threading.Lock()
        self._lock_factory = lock_factory

    def get(self, key: str) -> Any:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._lock_factory()
                self._locks[key] = lock
            return lock
