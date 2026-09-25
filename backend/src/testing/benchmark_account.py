from __future__ import annotations

from typing import Any

from system.usage_account import UsageAccount


class BenchmarkAccount(UsageAccount):
    kind = "benchmark"

    def __init__(self, run: dict, session: dict) -> None:
        self._run = run
        self._session = session

    def columns(self) -> dict[str, Any]:
        return {
            **super().columns(),
            "session_id": self._session["id"], "test_run_id": self._run["id"],
            "username": self._session["username"], "project_id": self._run["project_id"],
        }
