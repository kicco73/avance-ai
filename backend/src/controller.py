"""Composition root for the HTTP surface: one shared APIRouter, and
nothing on it that this file names.

Every controller now arrives through bus.POINT_HTTP_CONTROLLERS — the
channels register their own (webchat, whatsapp, listen, talk), the
authoring surface registers its own (avance_platform), and what is left
here is the handful of routes that are neither: the per-project views,
which any build has to answer.
"""
from __future__ import annotations

from fastapi import APIRouter

from auth.auth_controller import AuthController
from project.project_controller import ProjectController
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.api_state_controller import ApiStateController
from system.skills_controller import SkillsController
from system.bus_channel import BusChannel
from turn.session_controller import SessionController
from turn.turn_service import TurnService


class AvanceController(object):
    def __init__(
        self,
        turn_service: TurnService,
        project_service: ProjectService,
        bus_channel: BusChannel | None = None,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

        controllers = [
            ProjectController(turn_service, project_service),
            SessionController(turn_service),
            ApiStateController(turn_service, project_service),
            SkillsController(),
        ]
        # Signing in is not part of the authoring surface: the middleware
        # that gates every route in every build reads the same AuthService
        # this controller writes to (see auth/auth_controller.py), so every
        # build has one and this asks for it rather than working around
        # its absence.
        controllers.append(AuthController(bus.collect(POINT_CORE_SERVICES, {})["auth_service"]))
        # And whatever a skill registered for itself: a package that is
        # not in this build contributes nothing, so its routes are not
        # there to answer (see bus.POINT_HTTP_CONTROLLERS, system/skills.py).
        bus.collect(POINT_HTTP_CONTROLLERS, controllers)

        self.router = APIRouter()
        for controller in controllers:
            controller.register_routes(self.router)
        if bus_channel is not None:
            self.router.add_api_websocket_route("/api/core/bus", bus_channel.channel_loop)
