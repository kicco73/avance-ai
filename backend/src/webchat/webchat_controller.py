"""The chat window's own backend surface.

Packaged with the service rather than with the core's controllers, and
registered by webchat/skill.py — so a build without src/webchat/ does not
answer these routes at all, the same way it has nobody to run a turn.

What is here is a person having a conversation: opening a session,
reading its messages, sending an action, reacting to a reply, turning
audio on. What the *editor* does to a session — env, output, signals,
the dev-mode autotracking switch — left for
avance_platform/inspector_controller.py, where it belonged all along.
The first split of this file went by URL prefix, which is not what says
who a route belongs to — it let the editor's routes leave with the chat
window's, and left two of the chat window's own behind in the labelling
controller, where a build without an editor would have lost them.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from controllers.base_controller import BaseController, delete, get, post, put
from schemas import ActionRequest, ActuatorsRequest, AudioEnabledRequest, ReactionRequest
from system.session import Session
from turn.channels import NATIVE_CHAT
from turn.turn_service import TurnService


# XXX Compiled automaton requirement - do not touch.
# XXX The one thing every route below has in common: whoever called it is
# the chat window, speaking on the chat window's channel. Declared once,
# for the whole router, rather than in each method — a read here is not a
# read: get_messages calls open_if_needed, which runs a project's opening
# message as a real turn, through the same write admission gate a typed
# message goes through. Almost anything here can end up needing to know
# who is speaking, so a fourteenth route must not be addable without it.
#
# auth/auth_middleware.py used to do exactly this for *every*
# authenticated HTTP request in the system. That is what made core name
# webchat's channel, and what forced the editor's own routes — session
# lists, titles, test sessions — to carry a channel they have none of.
#
# async, and not incidentally: FastAPI runs a *sync* dependency in a
# worker thread, which gets a copy of the request's context — anything it
# sets there is discarded before the endpoint runs, silently. An async
# dependency runs in the request's own task, so the declaration reaches
# the endpoint whether the endpoint itself is sync or async.
async def _the_chat_window_is_speaking() -> None:
    Session().channel = NATIVE_CHAT


class WebchatController(BaseController):

    def __init__(self, turn_service: TurnService) -> None:
        self.turn_service = turn_service

    def register_routes(self, router: APIRouter) -> None:
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(
                path, member, methods=[method],
                dependencies=[Depends(_the_chat_window_is_speaking)], **kwargs,
            )

    @get("/api/skills/webchat/sessions/current")
    async def get_current_session(self, session_id: int | None = None):
        """Bootstrap endpoint: resolves (or creates) the active project's
        current writable session. Always a real, published-revision
        session — see the test-sessions/current endpoint for the draft equivalent."""
        return await self.turn_service.get_current_session_if_any_or_create_new(session_id)

    @post("/api/skills/webchat/sessions")
    async def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return await self.turn_service.create_session()

    @delete("/api/skills/webchat/sessions/{session_id}")
    def delete_session(self, session_id: int):
        """Deletes a session and all its messages/signals. Raises
        TurnServiceError (404) if it doesn't exist or belongs to someone
        else — handled by the global exception handler."""
        self.turn_service.delete_session(session_id)
        return {"success": True}

    @post("/api/skills/webchat/sessions/{session_id}/close")
    async def post_close_session(self, session_id: int):
        """The live chat's own "Close session" option — ends session_id
        without starting a replacement (see post_create_session above for
        that). Raises TurnServiceError (404) if it doesn't exist or
        belongs to someone else."""
        return await self.turn_service.close_session(session_id)

    @get("/api/skills/webchat/sessions/{session_id}/state")
    def get_session_state(self, session_id: int):
        return self.turn_service.get_state_for_session(session_id)

    @get("/api/skills/webchat/sessions/{session_id}/operator-state")
    def get_operator_state(self, session_id: int):
        """HumanOperatorChatView.vue's own state read — see TurnService.
        get_state_for_operator."""
        return self.turn_service.get_state_for_operator(session_id)

    @get("/api/skills/webchat/sessions/{session_id}/messages")
    async def get_messages(self, session_id: int):
        return await self.turn_service.get_messages(session_id)

    @post("/api/skills/webchat/sessions/{session_id}/actions")
    async def post_action(self, session_id: int, req: ActionRequest):
        try:
            return await self.turn_service.apply_manual_action(req.action_name, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/skills/webchat/sessions/{session_id}/audio")
    def get_session_audio(self, session_id: int):
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @put("/api/skills/webchat/sessions/{session_id}/audio")
    def put_session_audio(self, session_id: int, req: AudioEnabledRequest):
        self.turn_service.set_audio_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_audio_enabled(session_id)}

    @get("/api/skills/webchat/sessions/{session_id}/actuators")
    def get_actuators(self, session_id: int):
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @put("/api/skills/webchat/sessions/{session_id}/actuators")
    def put_actuators(self, session_id: int, req: ActuatorsRequest):
        self.turn_service.set_actuators_enabled(session_id, req.enabled)
        return {"enabled": self.turn_service.is_actuators_enabled(session_id)}

    @put("/api/skills/webchat/messages/{message_id}/reaction")
    def put_message_reaction(self, message_id: int, req: ReactionRequest):
        """Sets or (reaction: null) clears the user's own reaction to
        message_id — a bot message, chosen from the active project's
        `reactions` dict. TurnServiceError (404) is handled globally."""
        return self.turn_service.set_message_reaction(message_id, req.reaction)
