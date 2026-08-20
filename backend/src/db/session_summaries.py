from __future__ import annotations

from .models import SessionSummary


class SessionSummaryMixin:

    def create_session_summary(self, session_id: int) -> int:
        row = SessionSummary.create(session=session_id, content=None)
        return row.id

    def get_session_summary(self, session_id: int) -> dict | None:
        row = SessionSummary.get_or_none(SessionSummary.session == session_id)
        if row is None:
            return None
        return {'id': row.id, 'session_id': row.session_id, 'content': row.content}

    def set_session_summary_content(self, summary_id: int, content: str) -> None:
        SessionSummary.update(content=content).where(SessionSummary.id == summary_id).execute()

    def get_session_ids_with_summary(self, session_ids: list[int]) -> set[int]:
        if not session_ids:
            return set()
        rows = SessionSummary.select(SessionSummary.session).where(SessionSummary.session.in_(session_ids))
        return {row.session_id for row in rows}
