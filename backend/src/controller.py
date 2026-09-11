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

from project.project_controller import ProjectController
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_HTTP_CONTROLLERS
from system.skills_controller import SkillsController
from system.ws_notifications import WsNotifications
from turn.turn_service import TurnService


class AvanceController(object):
    def __init__(
        self,
        turn_service: TurnService,
        project_service: ProjectService,
        ws_notifications: WsNotifications | None = None,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

        controllers = [ProjectController(turn_service, project_service), SkillsController()]
        # And whatever a skill registered for itself: a package that is
        # not in this build contributes nothing, so its routes are not
        # there to answer (see bus.POINT_HTTP_CONTROLLERS, system/skills.py).
        bus.collect(POINT_HTTP_CONTROLLERS, controllers)

        self.router = APIRouter()
        for controller in controllers:
            controller.register_routes(self.router)
        if ws_notifications is not None:
            self.router.add_api_websocket_route("/ws/notifications", ws_notifications.channel_loop)
