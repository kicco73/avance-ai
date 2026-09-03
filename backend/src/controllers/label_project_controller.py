"""LabelProjectView.vue's own backend surface ("Label sessions") —
import/export/annotate/review past sessions, and the test-run/
state-aggregation machinery its own Performance tab drives.
"""
from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from urllib.parse import quote

from fastapi import HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from chat.chat_service import ChatService
from job import JobService
from project.project_service import ProjectService
from session import Session
from testing.test_service import TestService
from testing.last_status_broadcaster import LastStatusBroadcaster
from tracking.tracking_service import TrackingService
from schemas import (
    CommentRequest,
    CreateTestRequest,
    ExpectedSignalsRequest,
    ExpectedStateRequest,
    ReassignSessionsRequest,
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
        test_service: TestService,
        test_event_broadcaster: LastStatusBroadcaster,
        job_service: JobService,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.tracking_service = tracking_service
        self.test_service = test_service
        self.test_event_broadcaster = test_event_broadcaster
        self.job_service = job_service

    @get("/api/projects/{project_id}/tests/metrics", role="supervisor")
    def get_test_metrics(self, project_id: str, session_id: int | None = None):
        """Expert-annotation-vs-actual benchmark metrics for
        `project_id` — every annotated session, or (session_id given)
        just that one. The "Label sessions" view's Performance tab."""
        return self.chat_service.get_benchmark_metrics(project_id=project_id, session_id=session_id)

    @post("/api/projects/{project_id}/sessions/import", role="supervisor")
    async def post_import_sessions(self, project_id: str, files: list[UploadFile]):
        """The "Label sessions" view's own upload button — every selected
        file in one request, whichever mix of a .txt transcript and a
        "Download all" .json export it contains. Runs on the real job
        queue; this same response streams its progress SSE-style,
        ending with a chunk carrying the final {results, last_session_id}
        — no separate status endpoint, no separate connection."""
        uploads = [(file.filename or "", await file.read()) for file in files]
        job = self.tracking_service.build_import_sessions_job(project_id, uploads)
        return self.job_service.stream_progress(job)

    @delete("/api/projects/{project_id}/sessions/imported", role="supervisor")
    def delete_imported_sessions(self, project_id: str):
        """The "Label sessions" view's own "Delete all imported sessions"
        button — every imported session of the project, across every
        user, not just the current one's."""
        self.tracking_service.delete_imported_sessions(project_id)
        return {"success": True}

    @get("/api/projects/{project_id}/sessions/export", role="supervisor")
    def get_export_sessions(self, project_id: str, type: str | None = None):
        """The "Label sessions" view's own "Download all" button — every
        session of `project_id` (native and imported alike, same as
        ever, when `type` is omitted), or only `type` ('live' |
        'imported') when given — SessionsTree.vue always passes one,
        narrowing to whichever tab is currently showing."""
        payload = self.tracking_service.export_sessions(Session().user, project_id, type=type or ('live', 'imported'))
        content = json.dumps(payload, indent=2).encode("utf-8")
        encoded_project_id = quote(project_id)
        suffix = f"-{type}" if type else ""
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=\"sessions.json\"; filename*=UTF-8''{encoded_project_id}{suffix}-sessions.json"
            },
        )

    @put("/api/projects/{project_id}/sessions/reassign", role="supervisor")
    def put_sessions_reassign(self, project_id: str, req: ReassignSessionsRequest):
        """The "Label sessions" view's drag-and-drop between branches —
        `req.username` is whichever branch the sessions were dropped on,
        a "Test user N" one or any other imported username alike."""
        self.tracking_service.reassign_sessions_to_username(req.session_ids, req.username)
        return {"success": True}

    @delete("/api/projects/{project_id}/test-users/{test_user_seq}", role="supervisor")
    def delete_test_user(self, project_id: str, test_user_seq: int):
        self.tracking_service.delete_sessions_by_username(project_id, f"Test user {test_user_seq}")
        return {"success": True}

    @delete("/api/projects/{project_id}/sessions/users/{username}", role="supervisor")
    def delete_user_sessions(self, project_id: str, username: str):
        """The "Label sessions" view's per-branch × button for any
        non-live branch — an arbitrary imported username, not just a
        "Test user N" one (see delete_test_user above for that case)."""
        self.tracking_service.delete_sessions_by_username(project_id, username)
        return {"success": True}

    @delete("/api/chat/sessions/{session_id}")
    def delete_session(self, session_id: int):
        """Deletes a session and all its messages/signals. Raises
        ChatServiceError (404) if it doesn't exist or belongs to someone
        else — handled by the global exception handler."""
        self.chat_service.delete_session(session_id)
        return {"success": True}

    @post("/api/chat/sessions/{session_id}/close")
    async def post_close_session(self, session_id: int):
        """The live chat's own "Close session" option — ends session_id
        without starting a replacement (see chat_controller.py's own
        POST /api/chat/sessions for that). Raises ChatServiceError (404)
        if it doesn't exist or belongs to someone else."""
        return await self.chat_service.close_session(session_id)

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

    @delete("/api/projects/{project_id}/tests", role="supervisor")
    def delete_tests(self, project_id: str):
        self.test_service.reset_cache(project_id)
        return {"success": True}

    @delete("/api/projects/{project_id}/tests/jobs/{job_key}", role="supervisor")
    def delete_test_job(self, project_id: str, job_key: str):
        """Aborts the running job for job_key (the same "<strategy>:<node_id>"
        string the frontend already computes as its SSE cache key) — a
        no-op if nothing is currently tracked under that key."""
        self.test_service.abort_job(job_key)
        return {"success": True}

    @delete("/api/projects/{project_id}/tests/jobs", role="supervisor")
    def delete_all_test_jobs(self, project_id: str):
        """Aborts every currently tracked, still in-flight job across every
        node — the square "run all" button's own stop action."""
        self.test_service.abort_all_jobs()
        return {"success": True}

    @post("/api/projects/{project_id}/tests", role="supervisor")
    def post_test(self, project_id: str, req: CreateTestRequest):
        """Creates a Test and submits its replay job, returning
        immediately with status='pending' — or, for a single-session run
        whose exact (project/annotation state, strategy) was already
        replayed to completion, that cached run directly, with no new
        job submitted. TestServiceError is handled globally."""
        username = req.username if req.username is not None else Session().user
        try:
            return self.test_service.create_run(
                username, project_id, req.session_id, req.strategy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/tests/export", role="supervisor")
    def get_test_export(self, project_id: str):
        payload = self.test_service.export_results(project_id)
        content = json.dumps(payload, indent=2).encode("utf-8")
        encoded_project_id = quote(project_id)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=\"tests.json\"; filename*=UTF-8''{encoded_project_id}-tests.json"
            },
        )

    # Named get_test_record, not get_test: inspect.getmembers walks routes
    # alphabetically (see base_controller.py's own docstring), and "get_test"
    # would sort before — and so shadow — the literal get_test_export/
    # get_test_metrics routes above.
    @get("/api/projects/{project_id}/tests/{test_id}", role="supervisor")
    def get_test_record(self, project_id: str, test_id: int):
        """One Test, its domain data merged with its Job's
        lifecycle (status/progress/error/timestamps). TestServiceError
        (404 for an unknown test_id) is handled globally."""
        return self.test_service.get_run(test_id)

    @get("/api/projects/{project_id}/tests", role="supervisor")
    def get_tests(self, project_id: str, session_id: int | None = None, username: str | None = None):
        """Every Test for `project_id` with that exact
        session_id — None (the default) means every whole-project-scope
        run, not "no filter". `username`, when given, further narrows to
        that user's runs; omitted, no username filter is applied. Most
        recent first."""
        if username is None:
            return self.test_service.list_runs(project_id, session_id)
        return self.test_service.list_runs(project_id, session_id, username)

    @post("/api/projects/{project_id}/states/{state_key}/test", role="supervisor")
    def post_state_test(self, project_id: str, state_key: str, req: StateTestRequest):
        try:
            self.test_service.start_job(project_id, state_key, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/signals/{signal_name}/test", role="supervisor")
    def post_signal_test(self, project_id: str, signal_name: str, req: StateTestRequest):
        try:
            self.test_service.start_signal_job(project_id, signal_name, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/states/aggregation", role="supervisor")
    def post_states_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.test_service.start_all_states_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/signals/aggregation", role="supervisor")
    def post_signals_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.test_service.start_all_signals_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/root/aggregation", role="supervisor")
    def post_root_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.test_service.start_root_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/users/aggregation", role="supervisor")
    def post_users_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.test_service.start_users_aggregation_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/sessions/test", role="supervisor")
    def post_sessions_run(self, project_id: str, req: StateTestRequest):
        try:
            self.test_service.start_sessions_run_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/users/{username}/test", role="supervisor")
    def post_user_sessions_run(self, project_id: str, username: str, req: StateTestRequest):
        try:
            self.test_service.start_user_sessions_run_job(username, project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @get("/api/projects/{project_id}/aggregate-result", role="supervisor")
    def get_aggregate_result(self, project_id: str, kind: str, strategy: str, target: str | None = None):
        result = self.test_service.get_aggregate_result(project_id, kind, target, strategy)
        if result is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="No aggregate result for this key yet.")
        return result

    @get("/api/projects/{project_id}/test-events", role="supervisor")
    async def get_test_events(self, project_id: str, request: Request):
        username = Session().user
        connection = self.test_event_broadcaster.connect(username)

        async def events():
            try:
                for message in self.test_event_broadcaster.snapshot():
                    yield f"data: {json.dumps(message)}\n\n"
                while not await request.is_disconnected():
                    try:
                        message = await asyncio.wait_for(connection.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield f"data: {json.dumps(message)}\n\n"
            finally:
                self.test_event_broadcaster.disconnect(username, connection)

        return StreamingResponse(events(), media_type="text/event-stream")
