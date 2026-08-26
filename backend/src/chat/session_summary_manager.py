"""SessionSummaryManager: auto-queues a summary job the moment a native
session is discovered closed — see check_for_closed_sessions's own call
site. No periodic scan: the only discovery point is that hook.
"""
from __future__ import annotations

from ai.ai_service import AiService
from chat.session_manager import ChatSessionManager
from db import Db
from jobs import Job, JobQueue

SUMMARY_PROMPT = (
    "Summarize the salient points of the following conversation in a few "
    "sentences — what the user was trying to do, what was decided or "
    "resolved, and anything notably unresolved. Plain prose, no headers "
    "or bullet points."
)


class SessionSummaryJob(Job):

    def __init__(self, db: Db, ai_service: AiService, session_id: int, summary_id: int) -> None:
        super().__init__(key=f"session-summary:{session_id}", username="system")
        self._db = db
        self._ai_service = ai_service
        self._session_id = session_id
        self._summary_id = summary_id

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, []

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        messages = self._db.get_messages(self._session_id)
        history = [{'role': m['role'], 'content': m['content']} for m in messages]
        content = await self._ai_service.generate(SUMMARY_PROMPT, history)
        self._db.set_session_summary_content(self._summary_id, content)


class SessionSummaryManager:

    def __init__(
        self, db: Db, ai_service: AiService, job_queue: JobQueue, session_manager: ChatSessionManager,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._job_queue = job_queue
        self._session_manager = session_manager

    def check_for_closed_sessions(self, username: str, project_name: str) -> None:
        # Only 'live' — an imported session and a "Test" (draft) one
        # have no real usage timeline for "closed" to mean anything about.
        sessions = self._db.list_chat_sessions(username, project_name, type='live')
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
            self._job_queue.submit(
                SessionSummaryJob(self._db, self._ai_service, session['id'], summary_id)
            )
