from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class MetricResult(object):
    name: str
    value: float
    components: dict[str, float] = field(default_factory=dict)
    calculated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricWindow(object):
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class UserAnalyticsData(object):
    """Immutable-ish analytical view of one user/project.

    DataFrames are treated as read-only by metric calculators. They are built
    once by UserAnalyticsDataBuilder and shared by all calculators.
    """

    username: str
    project_name: str
    messages: pd.DataFrame
    sessions: pd.DataFrame
    signals: pd.DataFrame
    transitions: pd.DataFrame

    @property
    def user_messages(self) -> pd.DataFrame:
        if self.messages.empty:
            return self.messages
        return self.messages.loc[self.messages["role"].eq("user")]
