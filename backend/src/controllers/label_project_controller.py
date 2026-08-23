"""LabelProjectView.vue's own backend surface ("Label sessions") —
import/export/annotate/review past sessions, and the benchmark-run/
state-aggregation machinery its own Performance tab drives.
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

    @get("/api/projects/{project_name}/benchmark-metrics", role="supervisor")
    def get_benchmark_metrics(self, project_name: str, session_id: int | None = None):
        """Expert-annotation-vs-actual benchmark metrics for
        `project_name` — every annotated session, or (session_id given)
        just that one. The "Label sessions" view's Performance tab."""
        return self.chat_service.get_benchmark_metrics(project_name=project_name, session_id=session_id)

    @post("/api/projects/{project_name}/sessions/import", role="supervisor")
    async def post_import_sessions(self, project_name: str, files: list[UploadFile]):
        """The "Label sessions" view's own upload button — every selected
        file in one request, whichever mix of a .txt transcript and a
        "Download all" .json export it contains. All per-file/per-session
        dispatch and error handling happens server-side; the frontend
        just uploads and renders the returned per-item results."""
        uploads = [(file.filename or "", await file.read()) for file in files]
        return self.tracking_service.import_sessions_batch(Session().user, project_name, uploads)

    @get("/api/projects/{project_name}/sessions/export", role="supervisor")
    def get_export_sessions(self, project_name: str):
        """The "Label sessions" view's own "Download all" button — every
        session (native and imported alike) of `project_name`, as one
        JSON array."""
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
        """Deletes a session and all its messages/signals. Raises
        ChatServiceError (404) if it doesn't exist or belongs to someone
        else — handled by the global exception handler."""
        self.chat_service.delete_session(session_id)
        return {"success": True}

    @put("/api/chat/sessions/{session_id}/labeled", role="supervisor")
    def put_session_labeled(self, session_id: int, req: SetSessionLabeledRequest):
        """The "Label sessions" view's "Mark done" button. Raises
        ChatServiceError (404) for an unknown/not-yours session_id."""
        return self.chat_service.mark_session_labeled(session_id, req.labeled)

    @put("/api/chat/sessions/{session_id}/title", role="supervisor")
    def put_session_title(self, session_id: int, req: SetSessionTitleRequest):
        """The "Label sessions" view's own Info tab — see ChatService.
        set_session_title. Same 404 convention as put_session_labeled."""
        return self.chat_service.set_session_title(session_id, req.title)

    @put("/api/chat/sessions/{session_id}/comment", role="supervisor")
    def put_session_comment(self, session_id: int, req: CommentRequest):
        """The "Label sessions" view's Info tab — a whole-session note,
        distinct from put_message_comment's per-message one below."""
        return self.chat_service.set_session_comment(session_id, req.comment)

    @get("/api/chat/sessions/{session_id}/summary", role="supervisor")
    def get_session_summary(self, session_id: int):
        """{content: str | None}. Auto-queued the moment this session was
        discovered closed — never triggered by this endpoint itself, which only reads."""
        return self.chat_service.get_session_summary(session_id)

    @post("/api/chat/sessions/{session_id}/truncate", role="supervisor")
    async def post_truncate_session(self, session_id: int, req: TruncateSessionRequest):
        """"Restart from here": the live state may have moved backward,
        so the fresh payload is read back only once the mutation itself
        (see ChatService.truncate_session for its own synchronization) has completed."""
        try:
            await self.chat_service.truncate_session(session_id, req.timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.project_service.get_active_state_payload()

    @get("/api/chat/sessions/{session_id}/signals", role="supervisor")
    def get_session_signals(self, session_id: int):
        """The full Tracking event log for `session_id` (snapshots and
        transitions, chronological) — the "Label sessions" view
        reconstructs the timeline entirely client-side from this call."""
        return self.chat_service.get_session_signals(session_id)

    @put("/api/chat/messages/{message_id}/expected-state", role="supervisor")
    def put_message_expected_state(self, message_id: int, req: ExpectedStateRequest):
        """Sets or (expected_state: null) clears message_id's expert-
        annotated expected state — the "Label sessions" view's States
        tab. ChatServiceError (404/409/422) is handled globally."""
        return self.chat_service.set_message_expected_state(message_id, req.expected_state)

    @put("/api/chat/messages/{message_id}/expected-signals", role="supervisor")
    def put_message_expected_signals(self, message_id: int, req: ExpectedSignalsRequest):
        """Sets or clears message_id's expert-annotated expected signal
        values — the "Label sessions" view's Signals tab. Same error
        handling as put_message_expected_state."""
        return self.chat_service.set_message_expected_signals(message_id, req.expected_values)

    @put("/api/chat/messages/{message_id}/comment", role="supervisor")
    def put_message_comment(self, message_id: int, req: CommentRequest):
        """Sets or (comment: null/empty) clears message_id's expert-left
        free-text comment. Unlike the expected-state/signals endpoints,
        every message is a legitimate target: no 409 here, only 404."""
        return self.chat_service.set_message_comment(message_id, req.comment)

    @delete("/api/chat/sessions/{session_id}/annotations", role="supervisor")
    def delete_session_annotations(self, session_id: int):
        """Clears every expert annotation across session_id's Tracking
        rows — the "Label sessions" view's "Unlabel all" action.
        ChatServiceError (404) is handled globally."""
        self.chat_service.clear_session_annotations(session_id)
        return {"success": True}

    @post("/api/projects/{project_name}/benchmark-runs", role="supervisor")
    def post_benchmark_run(self, project_name: str, req: CreateBenchmarkRunRequest):
        """Creates a BenchmarkRun and submits its replay job, returning
        immediately with status='pending' — or, for a single-session run
        whose exact (project/annotation state, strategy) was already
        replayed to completion, that cached run directly, with no new
        job submitted. BenchmarkServiceError is handled globally."""
        username = req.username if req.username is not None else Session().user
        try:
            return self.benchmark_run_service.create_run(
                username, project_name, req.session_id, req.strategy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/benchmark-runs/{run_id}", role="supervisor")
    def get_benchmark_run(self, project_name: str, run_id: int):
        """One BenchmarkRun, its domain data merged with its Job's
        lifecycle (status/progress/error/timestamps). BenchmarkServiceError
        (404 for an unknown run_id) is handled globally."""
        return self.benchmark_run_service.get_run(run_id)

    @get("/api/projects/{project_name}/benchmark-runs", role="supervisor")
    def get_benchmark_runs(self, project_name: str, session_id: int | None = None, username: str | None = None):
        """Every BenchmarkRun for `project_name` with that exact
        session_id — None (the default) means every whole-project-scope
        run, not "no filter". `username`, when given, further narrows to
        that user's runs; omitted, no username filter is applied. Most
        recent first."""
        if username is None:
            return self.benchmark_run_service.list_runs(project_name, session_id)
        return self.benchmark_run_service.list_runs(project_name, session_id, username)

    @post("/api/projects/{project_name}/states/{state_key}/test", role="supervisor")
    def post_state_test(self, project_name: str, state_key: str, req: StateTestRequest):
        """Launches the "States" branch's aggregation job for one state.
        Returns immediately with the ephemeral job's id; poll GET
        .../state-jobs/{job_id} for its outcome."""
        try:
            job_id = self.benchmark_run_service.start_job(Session().user, project_name, state_key, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"job_id": job_id}

    @get("/api/projects/{project_name}/state-jobs/{job_id}", role="supervisor")
    def get_state_job(self, project_name: str, job_id: int):
        """One ephemeral 'state_aggregation' job's status/progress/
        result. None (never a 404) for an unknown job_id, e.g. after a
        backend restart — distinguishable from "in progress"/"completed"."""
        return self.benchmark_run_service.get_job_status(job_id)

    @post("/api/projects/{project_name}/users/aggregation", role="supervisor")
    def post_users_aggregation(self, project_name: str, req: StateTestRequest):
        """Launches the "Users" branch's aggregation job — a simple mean
        across one whole-project-scope run per distinct annotated user.
        Returns immediately with the ephemeral job's id; poll GET
        .../state-jobs/{job_id} (the job is generic, same as start_job's) for its outcome."""
        try:
            job_id = self.benchmark_run_service.start_users_aggregation_job(project_name, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"job_id": job_id}
