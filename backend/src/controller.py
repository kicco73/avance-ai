"""Composition root: builds the shared services once and registers each
screen-scoped controller under controllers/ onto one shared APIRouter,
in the list order below — see each controller's own module docstring
for which FE screen it maps to."""
from __future__ import annotations

from fastapi import APIRouter

from auth.auth_service import AuthService
from chat.chat_service import ChatService
from chat.ws_adapter import WsAdapter
from db import Db
from jobs import JobQueue
from listen.listen_service import ListenService
from project.project_service import ProjectService
from talk.talk_service import TalkService
from testing.test_service import TestService
from testing.last_status_broadcaster import LastStatusBroadcaster
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from tracking.tracking_service import TrackingService

from controllers.auth_controller import AuthController
from controllers.chat_controller import ChatController
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
        test_event_broadcaster: QueueProgressBroadcaster | LastStatusBroadcaster,
        job_queue: JobQueue,
        version: str,
        services_config: dict,
        whatsapp_service: WhatsAppService | None = None,
        ws_adapter: WsAdapter | None = None,
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
        self.job_queue = job_queue
        self.version = version

        self.chat = ChatController(chat_service, project_service, talk_service, listen_service)
        self.edit_project = EditProjectController(chat_service, project_service)
        self.label_project = LabelProjectController(
            chat_service, project_service, tracking_service, test_service, test_event_broadcaster, job_queue,
        )
        self.settings = SettingsController(
            chat_service, project_service, db, version, test_event_broadcaster, job_queue, services_config,
        )
        self.auth = AuthController(auth_service)
        self.user = UserController(auth_service)

        controllers = [self.chat, self.edit_project, self.label_project, self.settings, self.auth, self.user]
        # Opt-in channel (see docs/WHATSAPP.md): no service, no routes.
        self.whatsapp = WhatsAppController(whatsapp_service) if whatsapp_service is not None else None
        if self.whatsapp is not None:
            controllers.append(self.whatsapp)

        self.router = APIRouter()
        for controller in controllers:
            controller.register_routes(self.router)
        if ws_adapter is not None:
            self.router.add_api_websocket_route("/ws/chat", ws_adapter.chat_loop)
