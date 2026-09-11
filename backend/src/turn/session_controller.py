"""What anyone does *to a chat session*, addressed by its id.

Core, not a channel's. Reading a transcript, asking a session for its
state, turning its audio or actuators on, reacting to a message, closing
or deleting it: none of these is a conversation, and none of them asks
who is speaking. The editor's Test panel, the labelling screens, the app
store's preview and the chat window itself all do them, on sessions that
variously have a channel and have none.

What stayed with webchat is what genuinely speaks: opening or resuming a
*live* session, firing an action on one, and the operator's own view of
it. Those three reach SessionManager.require_active_session, which is the
one place a channel decides an outcome (see turn/sessions/
session_type_strategy.py) — and a caller who cannot name a channel is
refused there rather than served a default.
"""
from __future__ import annotations

from fastapi import APIRouter

from http import HTTPStatus

from fastapi import HTTPException

from controllers.base_controller import BaseController, delete, get, post, put
from schemas import ActionRequest, ActuatorsRequest, AudioEnabledRequest, ReactionRequest
from turn.turn_service import TurnService


class SessionController(BaseController):

    def __init__(self, turn_service: TurnService) -> None:
        self.turn_service = turn_service

    def register_routes(self, router: APIRouter) -> None:
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(path, member, methods=[method], **kwargs)

    @delete("/api/core/sessions/{session_id}")
    def delete_session(self, session_id: int):
        self.turn_service.delete_session(session_id)
        return {"success": True}

    @post("/api/core/sessions/{session_id}/close")
    async def post_close_session(self, session_id: int):
        return await self.turn_service.close_session(session_id)

    @post("/api/core/sessions/{session_id}/actions")
    async def post_action(self, session_id: int, req: ActionRequest):
        """Firing an action on a session that has no channel — a test or
        preview one, where SessionTypeStrategy.is_valid_write_target
        admits any caller. A *live* session refuses this route for the
        same reason it admits webchat's: the write asks who is speaking
        and this caller cannot say, so it is turned away rather than
        given a default (code session_channel_mismatch)."""
        try:
            return await self.turn_service.apply_manual_action(req.action_name, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/core/sessions/{session_id}/signals", role="supervisor")
    def get_session_signals(self, session_id: int):
        """The full Tracking event log for `session_id` (snapshots and
        transitions, chronological) — the "Label sessions" view
        reconstructs the timeline entirely client-side from this call.

        Core: the benchmark reads the same log to show what a test run
        did, and it is turn_service's either way."""
        return self.turn_service.get_session_signals(session_id)

    @post("/api/core/sessions/{session_id}/truncate")
    async def post_truncate_session(self, session_id: int, req: TruncateSessionRequest):
        """"Restart from here": the live state may have moved backward,
        so the fresh payload is read back only once the mutation itself
        (see TurnService.truncate_session for its own synchronization) has completed."""
        try:
            await self.turn_service.truncate_session(session_id, req.timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.platform_service.get_active_state_payload()

    @get("/api/core/sessions/{session_id}/state")
    def get_session_state(self, session_id: int):
        return self.turn_service.get_state_for_session(session_id)

    @get("/api/core/sessions/{session_id}/transcript")
    def get_transcript(self, session_id: int):
        return self.turn_service.read_transcript(session_id)

    @get("/api/core/sessions/{session_id}/audio")
    def get_session_audio(self, session_id: int):
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @put("/api/core/sessions/{session_id}/audio")
    def put_session_audio(self, session_id: int, req: AudioEnabledRequest):
        self.turn_service.set_audio_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @get("/api/core/sessions/{session_id}/actuators")
    def get_actuators(self, session_id: int):
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @put("/api/core/sessions/{session_id}/actuators")
    def put_actuators(self, session_id: int, req: ActuatorsRequest):
        self.turn_service.set_actuators_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @put("/api/core/messages/{message_id}/reaction")
    def put_message_reaction(self, message_id: int, req: ReactionRequest):
        return self.turn_service.set_message_reaction(message_id, req.reaction)
