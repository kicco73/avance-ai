"""SessionSummaryManager: auto-queues a summary job the moment a native
session is discovered closed — see check_for_closed_sessions's own call
site (ChatService.get_or_create_current_session). No periodic scan: the
only discovery point is that hook, exactly as it is today (see this
module's own docstring in the prompt this was built from — a scheduled
scan for a session nobody ever comes back to is a natural extension for
when scheduling infrastructure exists, not built here).
"""
from __future__ import annotations

from ai.ai_service import AiService
from chat.session_manager import ChatSessionManager
from db import Db
from jobs import JobQueue, JobWork, OnProgress

SUMMARY_PROMPT = (
    "Summarize the salient points of the following conversation in a few "
    "sentences — what the user was trying to do, what was decided or "
    "resolved, and anything notably unresolved. Plain prose, no headers "
    "or bullet points."
)


class SessionSummaryManager:

    def __init__(
        self, db: Db, ai_service: AiService, persisted_jobs: JobQueue, session_manager: ChatSessionManager,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._persisted_jobs = persisted_jobs
        self._session_manager = session_manager

    def check_for_closed_sessions(self, username: str, project_name: str) -> None:
        # Only 'native' — an imported session and a "Test" (draft) one
        # (see ChatSession.source) have no real usage timeline for
        # "closed" to mean anything about (see this module's own docstring).
        sessions = self._db.list_chat_sessions(username, project_name, source='native')
        session_ids = [session['id'] for session in sessions]
        already_summarized = self._db.get_session_ids_with_summary(session_ids)

        for session in sessions:
            if session['id'] in already_summarized:
                continue
            if self._session_manager.is_open(session):
                continue
            # Created immediately, before submit — its own existence is
            # what stops this same session from being queued again on the
            # next call, regardless of how the job itself turns out.
            summary_id = self._db.create_session_summary(session['id'])
            self._persisted_jobs.submit(
                kind='session_summary', reference_id=summary_id, total=1,
                work=self._build_work(session['id'], summary_id),
            )

    def _build_work(self, session_id: int, summary_id: int) -> JobWork:
        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            messages = self._db.get_messages(session_id)
            history = [{'role': m['role'], 'content': m['content']} for m in messages]
            content = await self._ai_service.generate(SUMMARY_PROMPT, history)
            self._db.set_session_summary_content(summary_id, content)
            on_progress(1)
            return None, None

        return work
