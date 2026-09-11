"""The chat window's own backend surface.

Packaged with the service rather than with the core's controllers, and
registered by webchat/skill.py — so a build without src/webchat/ does not
answer these routes at all, the same way it has nobody to run a turn.

What is here is a person *speaking*: opening or resuming a live session,
firing an action on one, and the operator's own view of it. Each reaches
SessionManager.require_active_session, where a live session admits a
write only from the channel that opened it — so each has to be able to
name a channel, which is what `_the_chat_window_is_speaking` declares
once below.

What merely addresses a session by id — its state, audio, actuators, a
reaction, closing or deleting it, and reading a transcript without
opening it — left for
turn/session_controller.py, and what the *editor* does to one (env,
output, signals, the dev-mode autotracking switch) for
avance_platform/inspector_controller.py. Both moves have the same reason
and the same history: splitting this file by URL prefix put routes here
that no channel owns, and the core frontend then had to name webchat in
order to read a transcript — which is to say webchat was not optional at
all.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from controllers.base_controller import BaseController, get, post
from schemas import ActionRequest
from system.session import Session
from turn.turn_service import TurnService


#: The channel these routes speak on: this package. Not a string that
#: happens to match it — the skill key is derived from the package name
#: too (see skills.Skill.__init_subclass__), so there is exactly one
#: place the name is written down, and it is the directory.
CHANNEL = __package__


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
    Session().channel = CHANNEL


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

    @get("/api/skills/webchat/sessions/{session_id}/messages")
    async def get_messages(self, session_id: int):
        """The chat window's own history, and not a read: get_messages
        opens the conversation if it has not started, which runs the
        project's opening message as a real turn through the same write
        admission gate a typed message goes through. Whoever is only
        looking at a transcript asks the core instead (see
        turn/session_controller.py's own transcript route)."""
        return await self.turn_service.get_messages(session_id)

    @get("/api/skills/webchat/sessions/{session_id}/operator-state")
    def get_operator_state(self, session_id: int):
        """HumanOperatorChatView.vue's own state read — see TurnService.
        get_state_for_operator."""
        return self.turn_service.get_state_for_operator(session_id)

    @post("/api/skills/webchat/sessions/{session_id}/actions")
    async def post_action(self, session_id: int, req: ActionRequest):
        try:
            return await self.turn_service.apply_manual_action(req.action_name, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

