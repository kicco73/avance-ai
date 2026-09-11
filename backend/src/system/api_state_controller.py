"""The frontend's boot state, and its readiness ping.

Core, not platform. It used to be a route of the authoring surface, on
the reasoning that a build without that surface leaves POINT_API_STATE's
contributors with nobody collecting them — which is true, and is the
wrong conclusion: the contributors are skills describing themselves to
whatever frontend is running, and every build has one. A product with no
editor still has to tell its own frontend that it is up, which budgets
apply, and which capabilities are installed.

It asks only core collaborators. The active state payload comes from
project_service's own inspector, the budgets from turn_service, and the
rest is whatever each installed skill contributed about itself.
"""
from __future__ import annotations

from controllers.base_controller import BaseController, get, post
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_API_STATE
from turn.turn_service import TurnService


class ApiStateController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

    @get("/api/core/ai/models")
    def get_ai_models(self):
        """The ai-service provider roster (name/model/ui_label/ui_description),
        whether auto mode is on, and which model is in effect right now
        either way — for the chat toolbar's model menu."""
        return self.turn_service.get_ai_models_info()

    @post("/api/core/ai/models/selection")
    def post_ai_model_selection(self, req: AiModelSelectionRequest):
        """Sets which model generate()/generate_stream() use: `index:
        null` for auto (the cascade's fallback order), or `index` into
        GET /api/core/ai/models' `models` to pin one directly."""
        try:
            self.turn_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_ai_models_info()

    @get("/api/core/ai/models/test")
    def get_ai_test_models(self):
        return self.turn_service.get_test_ai_models_info()

    @post("/api/core/ai/models/test/selection")
    def post_ai_test_model_selection(self, req: AiModelSelectionRequest):
        try:
            self.turn_service.select_test_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_test_ai_models_info()
    @get("/api/core/state")
    def get_state(self):
        """No `-> StatePayload` annotation: with no active project/state
        the payload lacks those fields.

        Every `<skill>_enabled` flag the chat toolbar reads is that
        skill's own contribution, `talk_enabled` included — it used to be
        the one exception, computed here from a name this file had no
        business knowing. What each of them means is unchanged: the active
        project's own declared level (project.services.<key>, see
        automaton/project_services.py) narrowing the server's own switch,
        never widening it."""
        try:
            payload = self.project_service.inspector.get_active_state_payload()
        except:
            payload = {}

        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        # Whatever else is running adds its own field: listen_enabled
        # comes from the Listen package when that package is there, and
        # simply isn't in the payload when it isn't.
        return bus.collect(POINT_API_STATE, payload)
