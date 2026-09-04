from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from jobs import CancelableJob, Task

if TYPE_CHECKING:
    from ai import AiService
    from db import Db
    from job import JobService

SESSION_REPORT_INSTRUCTIONS = (
    "You are given the full transcript of a closed chat session, turn by turn, "
    "each annotated with any tracked signal values and any state change recorded "
    "on that turn. In the 'text' field, write a very succinct report of the session — "
    "a few sentences at most, plain prose, no headers or bullet points. In the 'title' "
    "field, write a short, descriptive title for the session (a few words, no ending "
    "punctuation)."
)

AI_SUMMARY_INSTRUCTIONS = (
    "You are given the succinct reports of a user's most recent sessions with this "
    "app, most recent first. Write a very succinct general summary of this user's "
    "overall activity and standing with the app so far — a few sentences at most, "
    "plain prose, no headers or bullet points."
)


def build_session_report_prompt(db: "Db", session_id: int) -> str:
    messages = db.get_messages(session_id)
    tracking_by_message = {
        row["message_id"]: row for row in db.get_signals(session_id) if row["message_id"] is not None
    }
    lines = []
    for i, message in enumerate(messages, start=1):
        lines.append(f"[Turn {i}] {message['role']}: {message['content']}")
        tracking = tracking_by_message.get(message["id"])
        if tracking is None:
            continue
        if tracking["values"]:
            lines.append(f"  signals: {tracking['values']}")
        if tracking["old_state"] and tracking["new_state"] and tracking["old_state"] != tracking["new_state"]:
            lines.append(f"  state change: {tracking['old_state']} -> {tracking['new_state']} (action: {tracking['action']})")
    transcript = "\n".join(lines)
    return f"{SESSION_REPORT_INSTRUCTIONS}\n\n{transcript}"


def build_ai_summary_prompt(summaries: list[str]) -> str:
    listing = "\n\n".join(f"[Session {i}] {summary}" for i, summary in enumerate(summaries, start=1))
    return f"{AI_SUMMARY_INSTRUCTIONS}\n\n{listing}"


class SessionReportTask(Task):

    TYPE = "session-report"

    def __init__(self, key: str, username: str, payload: dict[str, Any], hydrator: "SessionReportHydrator") -> None:
        super().__init__(key=key, username=username)
        self._payload = payload
        self._hydrator = hydrator

    @classmethod
    def create(
        cls, *, session_id: int, project_id: str, username: str, hydrator: "SessionReportHydrator",
        id: str | int | None = None,
    ) -> "SessionReportTask":
        payload = {"session_id": session_id, "project_id": project_id}
        json.dumps(payload)
        return cls(cls.make_key(id), username, payload, hydrator)

    @property
    def project_id(self) -> str:
        return self._payload["project_id"]

    @property
    def ui_label(self) -> str:
        return f"Session report — session {self._payload['session_id']}"

    @property
    def ui_description(self) -> str:
        return f"Generates an AI summary report for closed session {self._payload['session_id']}."

    def dehydrate(self) -> dict[str, Any]:
        return dict(self._payload)

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        await self._hydrator.run(self._payload["session_id"])


class SessionReportHydrator:

    def __init__(self, db: "Db", ai_service: "AiService") -> None:
        self._db = db
        self._ai_service = ai_service

    def hydrate(self, key: str, username: str, payload: dict[str, Any]) -> SessionReportTask:
        for field in ("session_id", "project_id"):
            if field not in payload:
                raise ValueError(f"Task {key} payload is missing '{field}'.")
        return SessionReportTask(key, username, payload, self)

    async def run(self, session_id: int) -> None:
        session = self._db.get_chat_session(session_id)
        assert session is not None
        prompt_text = build_session_report_prompt(self._db, session_id)
        result = await self._ai_service.prompt(prompt_text, channels=["title"])
        self._db.set_session_summary(session_id, result["text"], title=result.get("title", "").strip())

        summaries = self._db.get_recent_session_summaries(session["username"], session["project_id"], limit=3)
        ai_summary_prompt = build_ai_summary_prompt(summaries)
        ai_summary = await self._ai_service.prompt(ai_summary_prompt)
        self._db.set_user_project_ai_summary(session["username"], session["project_id"], ai_summary)


class SessionReportScheduler:

    def __init__(self, job_service: "JobService", hydrator: SessionReportHydrator) -> None:
        self._job_service = job_service
        self._hydrator = hydrator

    def schedule(self, session: dict) -> None:
        if session["type"] != "live":
            return
        task = SessionReportTask.create(
            session_id=session["id"], project_id=session["project_id"], username=session["username"],
            hydrator=self._hydrator,
        )
        self._job_service.schedule(task, datetime.now(timezone.utc))
