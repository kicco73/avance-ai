"""What the Inspector reads and writes about a live session.

The design view's own surface: the env a session has accumulated, the
structured `output` of its last turns, the signals last computed for it,
and the dev-mode switch that freezes automatic state transitions.

It sat in webchat until now, and only because the split that created
that package went by URL prefix: everything under /api/chat/ was taken
to be the chat window's. It is not — this is the editor looking inside a
session, and a build with no authoring surface has nobody to look.
Nothing here is reachable from the chat window, and none of it goes
through require_active_session, which is the check that cares which
channel the caller is on. Reading someone's env is not speaking to them.

The paths still say /api/chat/ because changing them is a frontend
change and belongs with the migration to /api/skills/<skill>/ that
platform and testing have already had — label_project_controller.py is
in the same position.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException

from controllers.base_controller import BaseController, delete, get, post, put
from schemas import AutoTrackingRequest, SetEnvValueRequest
from turn.turn_service import TurnService


class InspectorController(BaseController):

    def __init__(self, turn_service: TurnService) -> None:
        self.turn_service = turn_service

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see TrackingService.run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.turn_service.get_latest_signals()

    @get("/api/chat/sessions/{session_id}/env")
    def get_env(self, session_id: int, message_id: int | None = None):
        """{"stored": ..., "action_set": ...} — session_id's own
        "environment" memory, split so the Inspector Env tab knows which
        section each value belongs in (only "stored" is editable)."""
        return self.turn_service.get_env(session_id, message_id)

    @get("/api/chat/sessions/{session_id}/output")
    def get_output(self, session_id: int, message_id: int | None = None):
        """{"output": {...}} — the raw structured `output` field values
        produced by the turn linked to message_id (or, with none given,
        the session's latest), for the Run Inspector's own Output card."""
        return self.turn_service.get_output(session_id, message_id)

    @delete("/api/chat/sessions/{session_id}/env")
    def clear_env(self, session_id: int):
        """Wipes every stored and action-set env key at once for
        session_id (see TurnService.clear_env)."""
        return self.turn_service.clear_env(session_id)

    @put("/api/chat/sessions/{session_id}/env/{key}")
    def put_env_value(self, session_id: int, key: str, req: SetEnvValueRequest):
        """Edits one stored env key (see TurnService.set_env_value) —
        the Inspector Env tab's own "click a value to edit it". Always
        current: there's no "editing history"."""
        try:
            return self.turn_service.set_env_value(session_id, key, req.value)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/chat/sessions/{session_id}/env/{key}")
    def delete_env_value(self, session_id: int, key: str):
        """Removes one stored env key outright (see TurnService.
        delete_env_key) — the Inspector Env tab's own delete button."""
        try:
            return self.turn_service.delete_env_key(session_id, key)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/chat/sessions/{session_id}/autotracking")
    def get_autotracking(self, session_id: int):
        """"Dev mode: freeze automatic state transitions" — EditProjectView.
        vue's own embedded "Test" chat only; a native/imported session is
        always auto-tracked (see TrackingService.process)."""
        return {"enabled": self.turn_service.is_auto_tracking_enabled(session_id)}

    @post("/api/chat/sessions/{session_id}/autotracking")
    def post_autotracking(self, session_id: int, req: AutoTrackingRequest):
        self.turn_service.set_auto_tracking_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_auto_tracking_enabled(session_id)}
