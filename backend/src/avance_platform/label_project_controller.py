"""LabelProjectView.vue's own backend surface ("Label sessions") —
import/export/annotate/review past sessions. The test-run/aggregation
machinery its Performance tab drives left with the package that runs it
(see testing/testing_controller.py): a build without benchmarking has
this screen and no /tests route behind it.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import quote

from fastapi import HTTPException, Response, UploadFile

from turn.turn_service import TurnService
from avance_platform.platform_service import PlatformService
from scheduler import SchedulerService
from system.session import Session
from tracking.tracking_service import TrackingService
from schemas import (
    CommentRequest,
    ExpectedSignalsRequest,
    ExpectedStateRequest,
    ReassignSessionsRequest,
    SetSessionLabeledRequest,
    SetSessionTitleRequest,
    TruncateSessionRequest,
)

from controllers.base_controller import BaseController, delete, get, post, put


class LabelProjectController(BaseController):

    def __init__(
        self,
        turn_service: TurnService,
        platform_service: PlatformService,
        tracking_service: TrackingService,
        scheduler_service: SchedulerService,
    ) -> None:
        self.turn_service = turn_service
        self.platform_service = platform_service
        self.tracking_service = tracking_service
        self.scheduler_service = scheduler_service

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
        return self.scheduler_service.stream_progress(job)

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
        TurnServiceError (404) if it doesn't exist or belongs to someone
        else — handled by the global exception handler."""
        self.turn_service.delete_session(session_id)
        return {"success": True}

    @post("/api/chat/sessions/{session_id}/close")
    async def post_close_session(self, session_id: int):
        """The live chat's own "Close session" option — ends session_id
        without starting a replacement (see chat_controller.py's own
        POST /api/chat/sessions for that). Raises TurnServiceError (404)
        if it doesn't exist or belongs to someone else."""
        return await self.turn_service.close_session(session_id)

    @put("/api/chat/sessions/{session_id}/labeled", role="supervisor")
    def put_session_labeled(self, session_id: int, req: SetSessionLabeledRequest):
        """The "Label sessions" view's "Mark done" button. Raises
        TurnServiceError (404) for an unknown/not-yours session_id."""
        return self.turn_service.mark_session_labeled(session_id, req.labeled)

    @put("/api/chat/sessions/{session_id}/title", role="supervisor")
    def put_session_title(self, session_id: int, req: SetSessionTitleRequest):
        """The "Label sessions" view's own Info tab — see TurnService.
        set_session_title. Same 404 convention as put_session_labeled."""
        return self.turn_service.set_session_title(session_id, req.title)

    @put("/api/chat/sessions/{session_id}/comment", role="supervisor")
    def put_session_comment(self, session_id: int, req: CommentRequest):
        """The "Label sessions" view's Info tab — a whole-session note,
        distinct from put_message_comment's per-message one below."""
        return self.turn_service.set_session_comment(session_id, req.comment)

    @post("/api/chat/sessions/{session_id}/truncate", role="supervisor")
    async def post_truncate_session(self, session_id: int, req: TruncateSessionRequest):
        """"Restart from here": the live state may have moved backward,
        so the fresh payload is read back only once the mutation itself
        (see TurnService.truncate_session for its own synchronization) has completed."""
        try:
            await self.turn_service.truncate_session(session_id, req.timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.platform_service.get_active_state_payload()

    @get("/api/chat/sessions/{session_id}/signals", role="supervisor")
    def get_session_signals(self, session_id: int):
        """The full Tracking event log for `session_id` (snapshots and
        transitions, chronological) — the "Label sessions" view
        reconstructs the timeline entirely client-side from this call."""
        return self.turn_service.get_session_signals(session_id)

    @put("/api/chat/messages/{message_id}/expected-state", role="supervisor")
    def put_message_expected_state(self, message_id: int, req: ExpectedStateRequest):
        """Sets or (expected_state: null) clears message_id's expert-
        annotated expected state — the "Label sessions" view's States
        tab. TurnServiceError (404/409/422) is handled globally."""
        return self.turn_service.set_message_expected_state(message_id, req.expected_state)

    @put("/api/chat/messages/{message_id}/expected-signals", role="supervisor")
    def put_message_expected_signals(self, message_id: int, req: ExpectedSignalsRequest):
        """Sets or clears message_id's expert-annotated expected signal
        values — the "Label sessions" view's Signals tab. Same error
        handling as put_message_expected_state."""
        return self.turn_service.set_message_expected_signals(message_id, req.expected_values)

    @put("/api/chat/messages/{message_id}/comment", role="supervisor")
    def put_message_comment(self, message_id: int, req: CommentRequest):
        """Sets or (comment: null/empty) clears message_id's expert-left
        free-text comment. Unlike the expected-state/signals endpoints,
        every message is a legitimate target: no 409 here, only 404."""
        return self.turn_service.set_message_comment(message_id, req.comment)

    @delete("/api/chat/sessions/{session_id}/annotations", role="supervisor")
    def delete_session_annotations(self, session_id: int):
        """Clears every expert annotation across session_id's Tracking
        rows — the "Label sessions" view's "Unlabel all" action.
        TurnServiceError (404) is handled globally."""
        self.turn_service.clear_session_annotations(session_id)
        return {"success": True}

