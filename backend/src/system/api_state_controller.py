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

from automaton.project_services import OptionalService
from controllers.base_controller import BaseController, get
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_API_STATE
from system.config_services import talk_configured
from turn.turn_service import TurnService


class ApiStateController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

    @get("/api/core/state")
    def get_state(self):
        """No `-> StatePayload` annotation: with no active project/state
        the payload lacks those fields. `talk_enabled` is what the active
        project's own declared level (project.services.talk — see
        automaton/project_services.py) makes of the server's own
        talk-service switch: a project can only narrow it, never turn it
        on. The chat toolbar's audio/spoken-text icons read that one
        combined flag rather than checking the project's setting
        separately."""
        try:
            payload = self.project_service.inspector.get_active_state_payload()
        except:
            payload = {}

        try:
            project_talk = self.project_service.get_active_automaton().services["talk"]
        except:
            project_talk = OptionalService()

        payload["talk_enabled"] = project_talk.narrow(talk_configured())
        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        # Whatever else is running adds its own field: listen_enabled
        # comes from the Listen package when that package is there, and
        # simply isn't in the payload when it isn't.
        return bus.collect(POINT_API_STATE, payload)
