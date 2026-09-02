from __future__ import annotations

from collections.abc import Callable

from jobs import CancelableJob
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class DeferredActuatorJob(CancelableJob):
    def __init__(self, act: Callable[[], None]) -> None:
        super().__init__(key=f"actuator-defer:{id(act)}", username="system")
        self._act = act

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self._act()
