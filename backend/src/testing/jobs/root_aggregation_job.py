from __future__ import annotations

from typing import TYPE_CHECKING

from jobs import CancelableJob
from session import Session

if TYPE_CHECKING:
    from testing.test_service import TestService


class RootAggregationJob(CancelableJob):

    def __init__(self, service: "TestService", strategy: str, branch_jobs: list[CancelableJob]) -> None:
        super().__init__(key=f"{strategy}:root", username=Session().user)
        self._service = service
        self._branch_jobs = tuple(branch_jobs)

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, self._branch_jobs

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        pass
