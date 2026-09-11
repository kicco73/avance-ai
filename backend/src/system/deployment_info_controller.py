"""What this deployment has, asked from any screen that shows it.

Which services it is configured with — the snapshot each installed skill
describes itself into (bus.POINT_CONFIG_SERVICES) — what the scheduler is
holding, what the AI providers have spent, and what version is running.

All describing, so core: it stays true with no panel in the build, and
the Settings screen that renders it is the shell that assembles
contributions, which is the core's job too. *Changing* a deployment —
backup, restore, wipe, clean — is an operation somebody performs, and
left with the panel that offers it.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, HTTPException

from controllers.base_controller import BaseController, get
from db import Db
from scheduler import SchedulerService

APP_NAME = "Avance"


class DeploymentInfoController(BaseController):

    def __init__(self, db: Db, version: str, scheduler_service: SchedulerService, services_config: dict) -> None:
        self.db = db
        self.version = version
        self.scheduler_service = scheduler_service
        self.services_config = services_config

    def register_routes(self, router: APIRouter) -> None:
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(path, member, methods=[method], **kwargs)

    @get("/api/core/settings/about", role="supervisor")
    def get_about(self):
        return {"name": APP_NAME, "version": self.version}

    @get("/api/core/settings/services", role="admin")
    def get_services(self):
        """Read-only snapshot of .config.yml's own service sections (see
        AppConfig.public_services_snapshot), one tab per section on the
        frontend."""
        return self.services_config

    @get("/api/core/settings/services/ai-usage", role="admin")
    def get_ai_usage(self):
        """Each ai-service provider's own token spend, one point per
        minute over the trailing 24h (see db/ai_usage.py)."""
        labels = [f"{p['driver']}/{p['model']}" for p in self.services_config["ai"]["providers"]]
        return self.db.get_ai_token_usage_snapshot(labels)

    @get("/api/core/settings/tasks", role="admin")
    def get_scheduled_tasks(self, status: str | None = None, order: str = "asc"):
        """Task rows for one status at a time, by run_at per `order` (see
        scheduler.SchedulerService.list_tasks). `payload` is omitted: it
        is the task type's own hydration data, not meant for display."""
        try:
            tasks = self.scheduler_service.list_tasks(status=status, order=order)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {
            "tasks": [
                {key: value for key, value in task.items() if key != "payload"}
                for task in tasks
            ]
        }

