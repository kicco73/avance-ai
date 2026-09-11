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

        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        # Whatever else is running adds its own field: listen_enabled
        # comes from the Listen package when that package is there, and
        # simply isn't in the payload when it isn't.
        return bus.collect(POINT_API_STATE, payload)
