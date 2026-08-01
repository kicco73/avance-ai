from __future__ import annotations

from typing import Protocol

from .dto import MetricResult, UserAnalyticsData


class AnalyticsDb(Protocol):
    def list_chat_sessions(self, username: str, project_name: str) -> list[dict[str, object]]: ...
    def get_messages(self, session_id: int, last_n: int | None = None, since: object | None = None) -> list[dict[str, object]]: ...
    def get_signals(self, session_id: int) -> list[dict[str, object]]: ...


class MetricCalculator(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def ui_label(self) -> str: ...

    @property
    def ui_description(self) -> str: ...

    def calculate(self, data: UserAnalyticsData) -> MetricResult: ...
