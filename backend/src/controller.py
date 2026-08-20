"""Composition root — main.py/conftest.py construct exactly one
AvanceController with the same 7 services this class always took, and
register its single .router, same as when every endpoint lived on one
class. Every actual endpoint now lives on one of four screen-scoped
controllers under controllers/ (see each one's own module docstring for
which FE screen it maps to):

- controllers/chat_controller.py — ChatController (ChatWindow.vue)
- controllers/edit_project_controller.py — EditProjectController
  (EditProjectView.vue, "Edit project")
- controllers/label_project_controller.py — LabelProjectController
  (LabelProjectView.vue, "Label sessions")
- controllers/settings_controller.py — SettingsController (Settings
  menu / Manage projects)

Splitting is purely organizational: every one of them still registers
onto this exact same shared APIRouter (see controllers/base_controller.
py's own BaseController.register_routes), in the exact same relative
order the two registration-order-sensitive route pairs always needed —
both pairs happen to already live entirely within one controller each
(see BaseController's own docstring), so nothing here has to coordinate
registration order *across* controllers at all; they're merged in
whatever order this file lists them in.
"""
from __future__ import annotations

from fastapi import APIRouter

from chat.chat_service import ChatService
from db import Db
from listen.listen_service import ListenService
from metrics.benchmark_run_service import BenchmarkRunService
from project.project_service import ProjectService
from talk.talk_service import TalkService
from tracking.tracking_service import TrackingService

from controllers.chat_controller import ChatController
from controllers.edit_project_controller import EditProjectController
from controllers.label_project_controller import LabelProjectController
from controllers.settings_controller import SettingsController


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
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service
        self.db = db
        self.benchmark_run_service = benchmark_run_service
        self.tracking_service = tracking_service

        self.chat = ChatController(chat_service, project_service, talk_service, listen_service)
        self.edit_project = EditProjectController(chat_service, project_service)
        self.label_project = LabelProjectController(
            chat_service, project_service, tracking_service, benchmark_run_service
        )
        self.settings = SettingsController(chat_service, project_service, db)

        self.router = APIRouter()
        for controller in (self.chat, self.edit_project, self.label_project, self.settings):
            controller.register_routes(self.router)
