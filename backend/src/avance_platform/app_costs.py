from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean, median

from db import Db
from system.ai_costs import COST_CURRENCY, AiCostPricing

DAYS_PER_WEEK = 7
HOURS_PER_DAY = 24


class CostScope(object):

    def __init__(self, kinds: tuple[str, ...] | None, session_type: str | None) -> None:
        self.kinds = kinds
        self.session_type = session_type


SCOPES: dict[str, CostScope] = {
    "app": CostScope(None, None),
    "test": CostScope(("benchmark",), None),
    "preview": CostScope(("session",), "preview"),
    "run": CostScope(("session",), "test"),
    "users": CostScope(("session",), "live"),
}
USERS = SCOPES["users"]
DISTRIBUTION_BINS = 10


class LiveTurns(object):

    def extra_per_turn(self, turn_costs: list[float], app_total: float) -> float:
        return 0.0


class LiveTurnsWithExtras(LiveTurns):

    def extra_per_turn(self, turn_costs: list[float], app_total: float) -> float:
        return (app_total - sum(turn_costs)) / len(turn_costs) if turn_costs else 0.0


TURN_DISTRIBUTIONS: dict[str, LiveTurns] = {"users": LiveTurns(), "app": LiveTurnsWithExtras()}


class AppCosts(object):

    def __init__(self, db: Db, services_config: dict) -> None:
        self._db = db
        self._pricing = AiCostPricing(services_config)

    def apps(self, username: str) -> list[dict]:
        return [{"id": app["id"], "label": app["ui_label"] or app["id"]} for app in self._db.list_projects_for_app_store(username)]

    def users(self, project_id: str) -> list[dict]:
        names = {user["id"]: user["name"] or user["email"] for user in self._db.list_users()}
        return [
            {"id": username, "label": names.get(username) or username}
            for username in self._db.get_ai_usage_usernames(project_id, USERS.kinds, USERS.session_type)
        ]

    def sessions(self, project_id: str, username: str) -> list[dict]:
        return [
            {"id": session_id, "label": self._session_label(session_id)}
            for session_id in self._db.get_ai_usage_session_ids(project_id, username, USERS.kinds, USERS.session_type)
        ]

    def series(
        self, days: int, project_id: str, scope_name: str, username: str | None = None, session_id: int | None = None,
    ) -> dict:
        scope = SCOPES.get(scope_name)
        if scope is None:
            raise ValueError(f"Unknown cost scope {scope_name!r}; expected one of {sorted(SCOPES)}.")
        filters = (project_id, username, session_id, scope.kinds, scope.session_type)
        costs = self._pricing.daily(self._db.get_ai_usage_by_day(days, *filters))
        now = datetime.utcnow()
        return {
            **costs,
            "summary": {
                "last_24_hours": self._cost_since(now - timedelta(hours=HOURS_PER_DAY), filters),
                "last_7_days": self._cost_since(now - timedelta(days=DAYS_PER_WEEK), filters),
                **self._means(costs["history"], now.date()),
            },
        }

    def _cost_since(self, since: datetime, filters: tuple) -> float:
        return sum(self._pricing.cost(row) or 0.0 for row in self._db.get_ai_usage_since(since, *filters))

    def turn_distribution(self, days: int, project_id: str, scope_name: str) -> dict:
        distribution = TURN_DISTRIBUTIONS.get(scope_name)
        if distribution is None:
            raise ValueError(f"No per-turn distribution for scope {scope_name!r}; expected one of {sorted(TURN_DISTRIBUTIONS)}.")
        by_turn: dict[str, float] = {}
        for row in self._db.get_ai_usage_by_turn(days, project_id, USERS.kinds, USERS.session_type):
            by_turn[row["turn_id"]] = by_turn.get(row["turn_id"], 0.0) + (self._pricing.cost(row) or 0.0)
        turn_costs = list(by_turn.values())
        app_total = sum(
            sum(point["values"].values())
            for point in self._pricing.daily(self._db.get_ai_usage_by_day(days, project_id))["history"]
        )
        extra = distribution.extra_per_turn(turn_costs, app_total)
        costs = [cost + extra for cost in turn_costs]
        return {
            "currency": COST_CURRENCY,
            "turns": len(costs),
            "extra_per_turn": extra,
            "mean": mean(costs) if costs else 0.0,
            "median": median(costs) if costs else 0.0,
            "bins": self._bins(costs),
        }

    @staticmethod
    def _bins(costs: list[float]) -> list[dict]:
        if not costs:
            return []
        low, high = min(costs), max(costs)
        width = (high - low) / DISTRIBUTION_BINS or 1.0
        counts = [0] * DISTRIBUTION_BINS
        for cost in costs:
            counts[min(int((cost - low) / width), DISTRIBUTION_BINS - 1)] += 1
        return [{"from": low + i * width, "to": low + (i + 1) * width, "count": count} for i, count in enumerate(counts)]

    def _session_label(self, session_id: int) -> str:
        session = self._db.get_chat_session(session_id)
        if session is None:
            return f"Session {session_id} (deleted)"
        return session["title"] or (str(session["datetime_start"])[:16] if session["datetime_start"] else f"Session {session_id}")

    @staticmethod
    def _means(history: list[dict], today: date) -> dict:
        per_day = {date.fromisoformat(point["timestamp"][:10]): sum(point["values"].values()) for point in history}
        covered_days = (today - min(per_day)).days + 1 if per_day else 0
        mean_per_day = sum(per_day.values()) / covered_days if covered_days else 0.0
        return {"mean_per_day": mean_per_day, "mean_per_week": mean_per_day * DAYS_PER_WEEK}
