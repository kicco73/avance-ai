from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from jobs import CancelableJob
from logging_factory import LoggerFactory

if TYPE_CHECKING:
    from chat.ws_adapter import WsAdapter

logger = LoggerFactory.get_logger(__name__)


class DeferredActuatorJob(CancelableJob):
    def __init__(self, act: Callable[[], None], username: str, ws_adapter: "WsAdapter | None") -> None:
        super().__init__(key=f"actuator-defer:{id(act)}", username=username)
        self._act = act
        self._ws_adapter = ws_adapter

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        on_enter = self._act()
        if on_enter and self._ws_adapter is not None:
            await self._ws_adapter.push(self.username, {"type": "notification", "on-enter": on_enter})
