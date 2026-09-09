"""Composition root: builds the shared services once and registers each
screen-scoped controller under controllers/ onto one shared APIRouter,
in the list order below — see each controller's own module docstring
for which FE screen it maps to."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from auth.auth_service import AuthService
from turn.turn_service import TurnService
from db import Db
from project.project_service import ProjectService
from scheduler import SchedulerService
from testing.test_service import TestService
from system.broadcaster import Broadcaster
from system.ws_notifications import WsNotifications
from tracking.tracking_service import TrackingService

from controllers.app_store_controller import AppStoreController
from controllers.auth_controller import AuthController
from controllers.platform_controller import PlatformController
from system import bus
from build import BuildService
from system.bus import POINT_HTTP_CONTROLLERS
from config import DEFAULT_APPS_DIR
from controllers.build_controller import BuildController
from controllers.edit_project_controller import EditProjectController
from controllers.label_project_controller import LabelProjectController
from controllers.settings_controller import SettingsController
from controllers.user_controller import UserController


class AvanceController(object):
    def __init__(
        self,
        turn_service: TurnService,
        project_service: ProjectService,
        db: Db,
        tracking_service: TrackingService,
        test_service: TestService,
        auth_service: AuthService,
        test_event_broadcaster: Broadcaster,
        scheduler_service: SchedulerService,
        version: str,
        services_config: dict,
        ws_notifications: WsNotifications | None = None,
        apps_dir: Path | None = None,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.db = db
        self.test_service = test_service
        self.tracking_service = tracking_service
        self.auth_service = auth_service
        self.test_event_broadcaster = test_event_broadcaster
        self.scheduler_service = scheduler_service
        self.version = version

        self.platform = PlatformController(turn_service, project_service)
        self.edit_project = EditProjectController(turn_service, project_service, scheduler_service)
        # XXX Compiled automaton requirement - do not touch.
        # XXX The Build view's Target step, wired to a real compile. The
        # directory it writes into is the one CompiledAutomatonLoader
        # reads from — one setting (build-service.apps-dir), never two.
        self.build = BuildController(BuildService(db, project_service, apps_dir or DEFAULT_APPS_DIR))
        self.label_project = LabelProjectController(
            turn_service, project_service, tracking_service, test_service, test_event_broadcaster, scheduler_service,
        )
        self.settings = SettingsController(
            turn_service, project_service, db, version, test_event_broadcaster, scheduler_service, services_config,
        )
        self.auth = AuthController(auth_service)
        self.user = UserController(auth_service)
        self.app_store = AppStoreController(turn_service, project_service)

        controllers = [
            self.platform, self.edit_project, self.build, self.label_project, self.settings, self.auth,
            self.user, self.app_store,
        ]
        # And whatever a skill registered for itself: a package that is
        # not in this build contributes nothing, so its routes are not
        # there to answer (see bus.POINT_HTTP_CONTROLLERS, skills.py).
        bus.collect(POINT_HTTP_CONTROLLERS, controllers)

        self.router = APIRouter()
        for controller in controllers:
            controller.register_routes(self.router)
        if ws_notifications is not None:
            self.router.add_api_websocket_route("/ws/notifications", ws_notifications.channel_loop)
