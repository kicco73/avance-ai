"""The chat window's own backend surface: everything under /api/chat/.

Packaged with the service rather than with the core's controllers, and
registered by webchat/skill.py — so a build without src/webchat/ does not
answer these routes at all, the same way it has nobody to run a turn.

The split is the URL prefix and nothing cleverer: /api/chat/* is the
native chat window talking about its own live session; /api/skills/platform/state,
/api/docs, /api/skills/platform/ai/models and the per-project views stayed behind, in
controllers/platform_controller.py, because WhatsApp and the editor
need them just as much.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException

from controllers.base_controller import BaseController, delete, get, post, put
from schemas import (
    ActionRequest,
    ActuatorsRequest,
    AudioEnabledRequest,
    AutoTrackingRequest,
    ReactionRequest,
    SetEnvValueRequest,
)
from turn.turn_service import TurnService


class WebchatController(BaseController):

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

    @get("/api/chat/session")
    async def get_current_session(self, session_id: int | None = None):
        """Bootstrap endpoint: resolves (or creates) the active project's
        current writable session. Always a real, published-revision
        session — see the test-sessions/current endpoint for the draft equivalent."""
        return await self.turn_service.get_current_session_if_any_or_create_new(session_id)

    @post("/api/chat/sessions")
    async def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return await self.turn_service.create_session()

    @get("/api/chat/sessions/{session_id}/state")
    def get_session_state(self, session_id: int):
        return self.turn_service.get_state_for_session(session_id)

    @get("/api/chat/sessions/{session_id}/operator-state")
    def get_operator_state(self, session_id: int):
        """HumanOperatorChatView.vue's own state read — see TurnService.
        get_state_for_operator."""
        return self.turn_service.get_state_for_operator(session_id)

    @get("/api/chat/sessions/{session_id}/messages")
    async def get_messages(self, session_id: int):
        return await self.turn_service.get_messages(session_id)

    @post("/api/chat/sessions/{session_id}/action")
    async def post_action(self, session_id: int, req: ActionRequest):
        try:
            return await self.turn_service.apply_manual_action(req.action_name, session_id)
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

    @get("/api/chat/sessions/{session_id}/audio")
    def get_session_audio(self, session_id: int):
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @post("/api/chat/sessions/{session_id}/audio")
    def post_session_audio(self, session_id: int, req: AudioEnabledRequest):
        self.turn_service.set_audio_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @get("/api/chat/sessions/{session_id}/actuators")
    def get_actuators(self, session_id: int):
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @post("/api/chat/sessions/{session_id}/actuators")
    def post_actuators(self, session_id: int, req: ActuatorsRequest):
        self.turn_service.set_actuators_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @put("/api/chat/messages/{message_id}/reaction")
    def put_message_reaction(self, message_id: int, req: ReactionRequest):
        """Sets or (reaction: null) clears the user's own reaction to
        message_id — a bot message, chosen from the active project's
        `reactions` dict. TurnServiceError (404) is handled globally."""
        return self.turn_service.set_message_reaction(message_id, req.reaction)
