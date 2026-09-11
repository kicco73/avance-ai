"""Deciding what this deployment runs, and with what.

Switching which project the server answers for, which model replies, what
role somebody has. The line this package sits on the far side of is
describe/decide: *what* projects exist, who the users are, how a project
is shaped — that is domain, it goes on being true with no panel in the
build, and it is the core's. Choosing among them is an operation somebody
performs from a screen, and the screen is here.

A delivered product does not decide: it runs the one project it was built
around (see product/skill.py), on the model its configuration names. That
is why none of this has to survive a build that drops the editor, and why
putting it in the core — which is where it spent an afternoon — made the
core answer questions a product has no one to ask.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, HTTPException

from automaton.automaton import Automaton
from auth.auth_service import AuthService
from controllers.base_controller import BaseController, get, post, put
from project.project_service import ProjectService
from schemas import AiModelSelectionRequest, SetUserRoleRequest
from system.web_session import WebSession
from turn.turn_service import TurnService


class DeploymentController(BaseController):

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService, auth_service: AuthService,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.auth_service = auth_service

    @post("/api/skills/platform/projects/{project_id}/activate")
    async def activate_project(self, project_id: str):
        """Makes `project_id` the one this server answers turns for."""
        try:
            await self.project_service.activate_project_idempotent(project_id, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True, "project_id": project_id}

    @get("/api/skills/platform/ai/models")
    def get_ai_models(self):
        """The ai-service provider roster (name/model/ui_label/ui_description),
        whether auto mode is on, and which model is in effect right now
        either way — for the chat toolbar's model menu."""
        return self.turn_service.get_ai_models_info()

    @post("/api/skills/platform/ai/models/selection")
    def post_ai_model_selection(self, req: AiModelSelectionRequest):
        """Sets which model generate()/generate_stream() use: `index:
        null` for auto (the cascade's fallback order), or `index` into
        GET /api/skills/platform/ai/models' `models` to pin one directly."""
        try:
            self.turn_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_ai_models_info()

    @get("/api/skills/platform/ai/models/test")
    def get_ai_test_models(self):
        return self.turn_service.get_test_ai_models_info()

    @post("/api/skills/platform/ai/models/test/selection")
    def post_ai_test_model_selection(self, req: AiModelSelectionRequest):
        try:
            self.turn_service.select_test_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_test_ai_models_info()
    @put("/api/skills/platform/users/{user_id}/role", role="admin")
    def put_user_role(self, user_id: str, req: SetUserRoleRequest):
        return self.auth_service.set_user_role(user_id, req.role)

    async def _activate_project(self, project_id: str, new_automaton: Automaton) -> None:
        async with self.turn_service.acquire_write(project_id):
            pass
