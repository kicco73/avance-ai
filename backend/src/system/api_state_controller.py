"""The frontend's boot state, and its readiness ping.

Core, because it is the deployment describing itself: which project it
serves, what state that project is in, which budgets apply, and what each
installed skill says about itself (bus.POINT_API_STATE). All of that is
domain — it goes on being true with no panel in the build — and every
client needs it before it can do anything at all.

What is *not* here, and was for an afternoon: choosing a model, listing
projects, switching between them. Describing is domain; deciding is a
panel, and lives with one.
"""
from __future__ import annotations

from controllers.base_controller import BaseController, get
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_API_STATE
from turn.turn_service import TurnService


class ApiStateController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

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
        try:
            payload["project_id"] = self.project_service.get_active_project_id()
        except Exception:
            payload["project_id"] = None
        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        return bus.collect(POINT_API_STATE, payload)
