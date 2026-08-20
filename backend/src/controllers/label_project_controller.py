"""LabelProjectView.vue's own backend surface ("Label sessions") —
import/export/annotate/review past sessions, and the benchmark-run/
state-aggregation machinery its own Performance tab drives. Split out of
what used to be one single AvanceController class in controller.py — see
that module's own docstring, and BaseController's for the shared
registration mechanism every *_controller.py shares.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import quote

from fastapi import HTTPException, Response, UploadFile

from chat.chat_service import ChatService
from metrics.benchmark_run_service import BenchmarkRunService
from project.project_service import ProjectService
from session import Session
from tracking.tracking_service import TrackingService
from schemas import (
    CommentRequest,
    CreateBenchmarkRunRequest,
    ExpectedSignalsRequest,
    ExpectedStateRequest,
    SessionImportJsonRequest,
    SetSessionLabeledRequest,
    SetSessionTitleRequest,
    StateTestRequest,
    TruncateSessionRequest,
)

from .base_controller import BaseController, delete, get, post, put


class LabelProjectController(BaseController):

    def __init__(
        self,
        chat_service: ChatService,
        project_service: ProjectService,
        tracking_service: TrackingService,
        benchmark_run_service: BenchmarkRunService,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.tracking_service = tracking_service
        self.benchmark_run_service = benchmark_run_service

    @get("/api/chat/benchmark-metrics")
    def get_benchmark_metrics(self, session_id: int | None = None, project_name: str | None = None):
        """Expert-annotation-vs-actual benchmark metrics (see
        metrics_framework/benchmark_metrics) for `project_name` (omitted:
        the active project) — every annotated session, or (session_id
        given) just that one. For the "Label sessions" view's Performance
        tab. ChatServiceError (404 for an unknown/not-yours session_id)
        is handled globally, see error_handlers.py."""
        return self.chat_service.get_benchmark_metrics(session_id, project_name=project_name)

    @post("/api/chat/sessions/import")
    async def post_import_session(self, file: UploadFile, project_name: str | None = None):
        """Imports a chat session from a plain-text transcript (see
        TrackingService.import_session/tracking.session_import.
        parse_transcript) — annotatable/testable without ever having run
        through a live conversation. `project_name` omitted falls back to
        the active project, same convention as POST /api/chat/sessions;
        LabelProjectView.vue's own caller always passes its own
        props.projectName explicitly instead, so importing into project
        A never silently lands in whichever project B happens to be
        globally active right now. No try/except: a malformed transcript
        raises TrackingServiceError, already handled by the global
        exception handler (see error_handlers.py)."""
        content = await file.read()
        text = content.decode("utf-8")
        session_id = self.tracking_service.import_session(
            Session().user, project_name or self.project_service.get_active_project_name(), text, title=file.filename
        )
        return {"success": True, "session_id": session_id}

    @post("/api/chat/sessions/import-json")
    def post_import_session_json(self, req: SessionImportJsonRequest, project_name: str | None = None):
        """Imports one session from the "Download all" JSON shape (see
        TrackingService.import_session_json/tracking.session_export) —
        the frontend's own batch-upload loop calls this once per session
        object found inside an uploaded .json file, same per-item
        try/except-and-continue convention it already uses per .txt file
        (see LabelProjectView.vue's own handleImportSession). `project_
        name` is a separate query parameter, not a field on `req` itself
        — `req.model_dump()` is passed straight through as the session's
        own content (see SessionImportJsonRequest's own docstring), so
        project_name has no business inside that shape. Same explicit-
        vs-active-project reasoning as post_import_session above. No
        try/except here either: a malformed session raises
        TrackingServiceError (400), same global-handler convention as
        post_import_session above."""
        session_id = self.tracking_service.import_session_json(
            Session().user, project_name or self.project_service.get_active_project_name(), req.model_dump()
        )
        return {"success": True, "session_id": session_id}

    @get("/api/chat/sessions/export")
    def get_export_sessions(self, project_name: str | None = None):
        """The "Label sessions" view's own "Download all" button — every
        session (native and imported alike) of `project_name` (omitted:
        the active project), as one JSON array (see TrackingService.
        export_sessions/tracking.session_export's own module docstring on
        the exact shape). Same Response-with-Content-Disposition
        convention as get_project's own zip download — built server-side
        as bytes, not streamed, since a project's own session history is
        never large enough to need it."""
        project_name = project_name or self.project_service.get_active_project_name()
        payload = self.tracking_service.export_sessions(Session().user, project_name)
        content = json.dumps(payload, indent=2).encode("utf-8")
        encoded_project_name = quote(project_name)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=\"sessions.json\"; filename*=UTF-8''{encoded_project_name}-sessions.json"
            },
        )

    @delete("/api/chat/sessions/{session_id}")
    def delete_session(self, session_id: int):
        """Deletes a session and all its messages/signals — see
        ChatService.delete_session. Raises ChatServiceError (404) if it
        doesn't exist or belongs to someone else — handled by the global
        exception handler (see error_handlers.py), no try/except needed here."""
        self.chat_service.delete_session(session_id)
        return {"success": True}

    @put("/api/chat/sessions/{session_id}/labeled")
    def put_session_labeled(self, session_id: int, req: SetSessionLabeledRequest):
        """The "Label sessions" view's own "Mark done" button — see
        ChatService.mark_session_labeled. Raises ChatServiceError (404)
        for an unknown/not-yours session_id, same convention as
        delete_session above."""
        return self.chat_service.mark_session_labeled(session_id, req.labeled)

    @put("/api/chat/sessions/{session_id}/title")
    def put_session_title(self, session_id: int, req: SetSessionTitleRequest):
        """The "Label sessions" view's own Info tab — see ChatService.
        set_session_title. Same 404 convention as put_session_labeled."""
        return self.chat_service.set_session_title(session_id, req.title)

    @put("/api/chat/sessions/{session_id}/comment")
    def put_session_comment(self, session_id: int, req: CommentRequest):
        """The "Label sessions" view's own Info tab — see ChatService.
        set_session_comment (a whole-session note, distinct from
        put_message_comment's own per-message one below). Same 404
        convention as put_session_labeled."""
        return self.chat_service.set_session_comment(session_id, req.comment)

    @get("/api/chat/sessions/{session_id}/summary")
    def get_session_summary(self, session_id: int):
        """{content: str | None} — see ChatService.get_session_summary.
        Auto-queued the moment this session was discovered closed (see
        chat/session_summary_manager.py) — never triggered by this
        endpoint itself, which only ever reads."""
        return self.chat_service.get_session_summary(session_id)

    @post("/api/chat/sessions/{session_id}/truncate")
    async def post_truncate_session(self, session_id: int, req: TruncateSessionRequest):
        """"Restart from here" (EditProjectView.vue's chat only) — see
        ChatService.truncate_session. Same lock/response shape as
        post_reset: the live state may have just moved backward, so the
        fresh payload is read back only once the mutation (which a
        concurrent chat turn also touches) has released the lock."""
        try:
            async with self.chat_service.lock:
                self.chat_service.truncate_session(session_id, req.timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.project_service.get_active_state_payload()

    @get("/api/chat/sessions/{session_id}/signals")
    def get_session_signals(self, session_id: int):
        """The full Tracking event log for `session_id` (snapshots and
        transitions alike, chronological) — for the "Label sessions"
        view, which reconstructs state/signal values at any point in the
        session's timeline entirely client-side from this one call."""
        return self.chat_service.get_session_signals(session_id)

    @put("/api/chat/messages/{message_id}/expected-state")
    def put_message_expected_state(self, message_id: int, req: ExpectedStateRequest):
        """Sets or (expected_state: null) clears message_id's expert-
        annotated expected state — the "Label sessions" view's States
        tab. ChatServiceError (404 unowned/unknown message, 409 not an
        evaluation point, 422 unknown state) is handled globally, see
        error_handlers.py."""
        return self.chat_service.set_message_expected_state(message_id, req.expected_state)

    @put("/api/chat/messages/{message_id}/expected-signals")
    def put_message_expected_signals(self, message_id: int, req: ExpectedSignalsRequest):
        """Sets or clears message_id's expert-annotated expected signal
        values — the "Label sessions" view's Signals tab. Same error
        handling as put_message_expected_state, plus 422 for an unknown
        signal name or an out-of-range value."""
        return self.chat_service.set_message_expected_signals(message_id, req.expected_values)

    @put("/api/chat/messages/{message_id}/comment")
    def put_message_comment(self, message_id: int, req: CommentRequest):
        """Sets or (comment: null/empty) clears message_id's expert-left
        free-text comment — the "Label sessions" view's per-message
        comment bubble. Unlike put_message_expected_state/
        put_message_expected_signals, every message is a legitimate
        target: no 409 here, only the usual 404 for an unowned/unknown
        message (handled globally, see error_handlers.py)."""
        return self.chat_service.set_message_comment(message_id, req.comment)

    @delete("/api/chat/sessions/{session_id}/annotations")
    def delete_session_annotations(self, session_id: int):
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Tracking rows —
        the "Label sessions" view's "Unlabel all" action, fired only
        after its own confirmation dialog. ChatServiceError (404 for an
        unknown/not-yours session_id) is handled globally, see
        error_handlers.py."""
        self.chat_service.clear_session_annotations(session_id)
        return {"success": True}

    @post("/api/projects/{project_name}/benchmark-runs")
    def post_benchmark_run(self, project_name: str, req: CreateBenchmarkRunRequest):
        """Creates a BenchmarkRun and submits its replay job — returns
        immediately with status='pending' (see BenchmarkRunService.
        create_run), before the job's own worker thread has actually
        started it. BenchmarkServiceError (see metrics/benchmark_errors.py)
        is handled globally, see error_handlers.py."""
        try:
            return self.benchmark_run_service.create_run(
                Session().user, project_name, req.session_id, req.strategy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/benchmark-runs/{run_id}")
    def get_benchmark_run(self, project_name: str, run_id: int):
        """One BenchmarkRun, its own domain data merged with its Job's
        lifecycle (status/progress/error/timestamps) — see
        BenchmarkRunService.get_run. BenchmarkServiceError (404 for an
        unknown run_id) is handled globally, see error_handlers.py."""
        return self.benchmark_run_service.get_run(run_id)

    @get("/api/projects/{project_name}/benchmark-runs")
    def get_benchmark_runs(self, project_name: str, session_id: int | None = None):
        """Every BenchmarkRun for `project_name` with that exact
        session_id — None (the default) means every whole-project-scope
        run, not "no filter" (same convention as everywhere else in this
        system — see BenchmarkRunService.list_runs). Most recent first."""
        return self.benchmark_run_service.list_runs(project_name, session_id)

    @post("/api/projects/{project_name}/states/{state_key}/test")
    def post_state_test(self, project_name: str, state_key: str, req: StateTestRequest):
        """Launches the "Stati" branch's own aggregation job for one state
        — see BenchmarkRunService.start_job. Returns immediately with the
        ephemeral job's own id; poll GET .../state-jobs/{job_id} for its
        outcome (see get_state_job below)."""
        try:
            job_id = self.benchmark_run_service.start_job(Session().user, project_name, state_key, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"job_id": job_id}

    @get("/api/projects/{project_name}/state-jobs/{job_id}")
    def get_state_job(self, project_name: str, job_id: int):
        """One ephemeral 'state_aggregation' job's own status/progress/
        result — see BenchmarkRunService.get_job_status. None (never a
        404) for an unknown job_id, e.g. after a backend restart — the
        ephemeral queue's own JobSink already returns None for that case,
        distinguishable from "in progress"/"completed" by every caller."""
        return self.benchmark_run_service.get_job_status(job_id)
