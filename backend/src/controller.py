"""Composition root: builds the shared services once and registers each
screen-scoped controller under controllers/ onto one shared APIRouter,
in the list order below — see each controller's own module docstring
for which FE screen it maps to."""
from __future__ import annotations

from fastapi import APIRouter

from auth.auth_service import AuthService
from chat.chat_service import ChatService
from db import Db
from listen.listen_service import ListenService
from metrics.benchmark_run_service import BenchmarkRunService
from project.project_service import ProjectService
from talk.talk_service import TalkService
from tracking.tracking_service import TrackingService

from controllers.auth_controller import AuthController
from controllers.chat_controller import ChatController
from controllers.edit_project_controller import EditProjectController
from controllers.label_project_controller import LabelProjectController
from controllers.settings_controller import SettingsController
from controllers.user_controller import UserController


class AvanceController(object):
    def __init__(
        self,
        chat_service: ChatService,
        project_service: ProjectService,
        talk_service: TalkService | None,
        listen_service: ListenService | None,
        db: Db,
        tracking_service: TrackingService,
        benchmark_run_service: BenchmarkRunService,
        auth_service: AuthService,
        version: str,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service
        self.db = db
        self.benchmark_run_service = benchmark_run_service
        self.tracking_service = tracking_service
        self.auth_service = auth_service
        self.version = version

        self.chat = ChatController(chat_service, project_service, talk_service, listen_service)
        self.edit_project = EditProjectController(chat_service, project_service)
        self.label_project = LabelProjectController(
            chat_service, project_service, tracking_service, benchmark_run_service
        )
        self.settings = SettingsController(chat_service, project_service, db, version)
        self.auth = AuthController(auth_service)
        self.user = UserController(db)

        self.router = APIRouter()
        for controller in (self.chat, self.edit_project, self.label_project, self.settings, self.auth, self.user):
            controller.register_routes(self.router)
