"""Composition root: builds the shared services once and registers each
screen-scoped controller under controllers/ onto one shared APIRouter,
in the list order below — see each controller's own module docstring
for which FE screen it maps to."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from auth.auth_service import AuthService
from chat.chat_service import ChatService
from chat.ws_notifications import WsNotifications
from db import Db
from listen.listen_service import ListenService
from project.project_service import ProjectService
from scheduler import SchedulerService
from talk.talk_service import TalkService
from testing.test_service import TestService
from broadcaster import Broadcaster
from tracking.tracking_service import TrackingService

from controllers.app_store_controller import AppStoreController
from controllers.auth_controller import AuthController
from controllers.chat_controller import ChatController
from build import BuildService
from config import DEFAULT_APPS_DIR
from controllers.build_controller import BuildController
from controllers.edit_project_controller import EditProjectController
from controllers.label_project_controller import LabelProjectController
from controllers.settings_controller import SettingsController
from controllers.user_controller import UserController
from controllers.whatsapp_controller import WhatsAppController
from whatsapp.whatsapp_service import WhatsAppService


class AvanceController(object):
    def __init__(
        self,
        chat_service: ChatService,
        project_service: ProjectService,
        talk_service: TalkService | None,
        listen_service: ListenService | None,
        db: Db,
        tracking_service: TrackingService,
        test_service: TestService,
        auth_service: AuthService,
        test_event_broadcaster: Broadcaster,
        scheduler_service: SchedulerService,
        version: str,
        services_config: dict,
        whatsapp_service: WhatsAppService | None = None,
        ws_notifications: WsNotifications | None = None,
        apps_dir: Path | None = None,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service
        self.db = db
        self.test_service = test_service
        self.tracking_service = tracking_service
        self.auth_service = auth_service
        self.test_event_broadcaster = test_event_broadcaster
        self.scheduler_service = scheduler_service
        self.version = version

        self.chat = ChatController(chat_service, project_service, talk_service, listen_service)
        self.edit_project = EditProjectController(chat_service, project_service, scheduler_service)
        # XXX Compiled automaton requirement - do not touch.
        # XXX The Build view's Target step, wired to a real compile. The
        # directory it writes into is the one CompiledAutomatonLoader
        # reads from — one setting (build-service.apps-dir), never two.
        self.build = BuildController(BuildService(db, project_service, apps_dir or DEFAULT_APPS_DIR))
        self.label_project = LabelProjectController(
            chat_service, project_service, tracking_service, test_service, test_event_broadcaster, scheduler_service,
        )
        self.settings = SettingsController(
            chat_service, project_service, db, version, test_event_broadcaster, scheduler_service, services_config,
        )
        self.auth = AuthController(auth_service)
        self.user = UserController(auth_service)
        self.app_store = AppStoreController(chat_service, project_service)

        controllers = [
            self.chat, self.edit_project, self.build, self.label_project, self.settings, self.auth,
            self.user, self.app_store,
        ]
        # Opt-in channel (see docs/WHATSAPP.md): no service, no routes.
        self.whatsapp = WhatsAppController(whatsapp_service) if whatsapp_service is not None else None
        if self.whatsapp is not None:
            controllers.append(self.whatsapp)

        self.router = APIRouter()
        for controller in controllers:
            controller.register_routes(self.router)
        if ws_notifications is not None:
            self.router.add_api_websocket_route("/ws/notifications", ws_notifications.channel_loop)
