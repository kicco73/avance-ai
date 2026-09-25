from __future__ import annotations

from avance_platform.app_costs import AppCosts
from controllers.base_controller import BaseController, get
from db import Db
from system.web_session import WebSession

DEFAULT_COST_DAYS = 30


class CostsController(BaseController):

    def __init__(self, db: Db, services_config: dict) -> None:
        self.db = db
        self.services_config = services_config

    @property
    def costs(self) -> AppCosts:
        return AppCosts(self.db, self.services_config)

    @get("/api/skills/platform/costs/apps", role="admin")
    def get_apps(self):
        return {"apps": self.costs.apps(WebSession().user)}

    @get("/api/skills/platform/costs/apps/{project_id}/users", role="admin")
    def get_users(self, project_id: str):
        return {"users": self.costs.users(project_id)}

    @get("/api/skills/platform/costs/apps/{project_id}/users/{username}/sessions", role="admin")
    def get_sessions(self, project_id: str, username: str):
        return {"sessions": self.costs.sessions(project_id, username)}

    @get("/api/skills/platform/costs/series", role="admin")
    def get_series(
        self, project_id: str, scope: str = "app", username: str | None = None, session_id: int | None = None,
        days: int = DEFAULT_COST_DAYS,
    ):
        return self.costs.series(days, project_id, scope, username, session_id)

    @get("/api/skills/platform/costs/turns", role="admin")
    def get_turn_distribution(self, project_id: str, scope: str = "app", days: int = DEFAULT_COST_DAYS):
        return self.costs.turn_distribution(days, project_id, scope)
