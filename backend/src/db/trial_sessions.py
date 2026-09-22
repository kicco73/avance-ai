from __future__ import annotations

from .instrumentation import instrument_queries, write
from .models import TrialSession


@instrument_queries
class TrialSessionMixin:

    def count_trial_sessions(self, username: str, project_id: str) -> int:
        return TrialSession.select().where(
            (TrialSession.user == username) & (TrialSession.project == project_id)
        ).count()

    def count_trial_sessions_by_project(self, username: str) -> dict[str, int]:
        """Every project this user has ever started a test session on,
        mapped to how many — one query for a whole app-store listing."""
        counts: dict[str, int] = {}
        for row in TrialSession.select(TrialSession.project).where(TrialSession.user == username):
            counts[row.project_id] = counts.get(row.project_id, 0) + 1
        return counts

    @write
    def record_trial_session(self, username: str, project_id: str, revision: int | None) -> None:
        TrialSession.create(user=username, project=project_id, revision=revision)
