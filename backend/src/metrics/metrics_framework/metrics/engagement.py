from __future__ import annotations

from ..dto import MetricResult, UserAnalyticsData
from ..normalization import Normalizer
from .base import BaseMetric


class EngagementMetric(BaseMetric):
    """Overall user participation, weighted across message and session
    volume. References are configurable saturation points, not claims
    about an ideal user."""

    def __init__(self, message_reference: int = 100, session_reference: int = 10) -> None:
        self._message_reference = message_reference
        self._session_reference = session_reference

    @property
    def name(self) -> str:
        return "engagement"

    @property
    def ui_label(self) -> str:
        return "Engagement"

    @property
    def ui_description(self) -> str:
        return "How much the user interacts with the system, based on message and session volume."

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        messages = float(len(data.user_messages))
        sessions = float(len(data.sessions))
        message_score = Normalizer.ratio(messages, self._message_reference)
        session_score = Normalizer.ratio(sessions, self._session_reference)
        value = message_score * 0.6 + session_score * 0.4
        return self.result(self.name, value, {
            "user_messages": message_score,
            "sessions": session_score,
        })
