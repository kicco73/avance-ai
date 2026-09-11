"""The frontend's boot state, and its readiness ping.

Core, because it is the deployment describing itself: which project it
serves, what state that project is in, which budgets apply, and what each
installed skill says about itself (bus.POINT_API_STATE). All of that is
domain — it goes on being true with no panel in the build — and every
client needs it before it can do anything at all.

What is *not* here, and was for an afternoon: choosing a model, listing
projects, switching between them. Describing is domain; deciding is a
panel, and lives with one (see avance_platform/deployment_controller.py).
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException

from controllers.base_controller import BaseController, get
from project.project_service import ProjectService
from system import bus
from system.bus import POINT_API_STATE
from db import Db
from scheduler import SchedulerService
from turn.turn_service import TurnService


APP_NAME = "Avance"


class ApiStateController(BaseController):

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService, db: Db,
        version: str, scheduler_service: SchedulerService, services_config: dict,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.db = db
        self.version = version
        self.scheduler_service = scheduler_service
        self.services_config = services_config

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

        # Which project this deployment is serving. A client has to know
        # what it is talking about before it can talk, and asking the
        # authoring surface for the list would mean a delivered product
        # could not find out — it runs one project and this names it.
        try:
            payload["project_id"] = self.project_service.get_active_project_id()
        except Exception:
            payload["project_id"] = None
        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        # Whatever else is running adds its own field: listen_enabled
        # comes from the Listen package when that package is there, and
        # simply isn't in the payload when it isn't.
        return bus.collect(POINT_API_STATE, payload)
