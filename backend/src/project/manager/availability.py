from __future__ import annotations

from http import HTTPStatus

from db import Db
from events import AvailabilityChanged, ProjectRevisionBuildFailed, publish, subscribe
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError

from ..health import BuildOutcome, ProjectHealthChecker, broken_fields
from ..archive.automaton_loader import AutomatonLoader

logger = LoggerFactory.get_logger(__name__)


class ProjectBroken(ServiceError):
    """What every read endpoint of the design view answers with while the
    stored index.yml does not build. It carries the same `problems` a
    build error does — one entry per problem, each with its own line — so
    the view can offer them the way it offers warnings: a list you click
    through, not a paragraph you read and then go hunting."""

    def __init__(self, outcome: BuildOutcome) -> None:
        super().__init__(outcome.error or "", status_code=HTTPStatus.CONFLICT, code="project_broken")
        self._outcome = outcome

    def fields(self) -> dict[str, object]:
        raw = {"file": self._outcome.file, "line": self._outcome.line, "problems": self._outcome.problems}
        return {key: value for key, value in raw.items() if value}


class ProjectAvailability:
    def __init__(self, db: Db, health_checker: ProjectHealthChecker, automaton_loader: AutomatonLoader) -> None:
        self._db = db
        self._health_checker = health_checker
        self._automaton_loader = automaton_loader
        self._recomputing: set[str] = set()

    def recompute(self, project_id: str) -> None:
        self._recomputing.add(project_id)
        try:
            health = self._health_checker.check(project_id)

            if self._db.get_manually_paused(project_id):
                available, reason = False, "Manually paused."
            elif health.published is not None and health.published.error is not None:
                available, reason = False, health.published.error
            else:
                available, reason = True, None

            current = self._db.get_project_availability(project_id)
            if current is None:
                return
            was_paused, _ = current
            if was_paused == (not available):
                return
            self._db.set_project_availability(project_id, is_paused=not available, paused_reason=reason)
            publish(AvailabilityChanged(project_id=project_id, available=available))
        finally:
            self._recomputing.discard(project_id)

    def recompute_all(self) -> None:
        for project_id in self._db.list_projects():
            try:
                self.recompute(project_id)
            except Exception:
                logger.exception(
                    "recompute_availability failed for project '%s' during the boot-time sweep.", project_id
                )

    def ensure_project_not_broken(self, project_id: str) -> None:
        if not self._db.project_exists(project_id):
            return
        health = self._health_checker.current(project_id)
        if health.draft.error is not None:
            raise ProjectBroken(health.draft)

    def register_cascade(self) -> None:
        subscribe(ProjectRevisionBuildFailed, self._on_revision_build_failed)

    def _on_revision_build_failed(self, event: ProjectRevisionBuildFailed) -> None:
        if event.project_id in self._recomputing:
            return
        try:
            self.recompute(event.project_id)
        except Exception:
            logger.exception(
                "recompute_availability failed while reacting to a lazy build failure for '%s' (revision %s).",
                event.project_id, event.revision,
            )

    @staticmethod
    def project_status(is_paused: bool, manually_paused: bool) -> str:
        if manually_paused:
            return "manually_paused"
        if is_paused:
            return "paused"
        return "running"

    def get_runtime_status(self) -> list[dict]:
        return [
            {
                "id": row["id"],
                "status": self.project_status(row["is_paused"], row["manually_paused"]),
                "paused_reason": row["paused_reason"],
                "revision": row["revision"],
                "published_revision": row["published_revision"],
                **self._health_fields(row["id"]),
            }
            for row in self._db.list_projects_runtime_status()
        ]

    def _health_fields(self, project_id: str) -> dict:
        """What "Manage projects" reads to draw a row's broken/warning
        badge. One row and the whole list say it the same way, so
        replacing a single row (what pause/resume answer with) can never
        drop a badge the list had drawn."""
        health = self._health_checker.current(project_id)
        return {"broken": broken_fields(health)}

    def _current_status(self, project_id: str) -> str:
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_id) or (False, None)
        manually_paused = self._db.get_manually_paused(project_id) or False
        return self.project_status(is_paused, manually_paused)

    def set_manually_paused(self, project_id: str) -> dict:
        status = self._current_status(project_id)
        if status != "running":
            raise ValueError(f"Project '{project_id}' isn't running (status: '{status}') — can't be manually paused.")
        self._db.set_manually_paused(project_id, True)
        self.recompute(project_id)
        return self.get_project_runtime_status(project_id)

    def set_manually_running(self, project_id: str) -> dict:
        status = self._current_status(project_id)
        if status != "manually_paused":
            raise ValueError(f"Project '{project_id}' isn't manually paused (status: '{status}') — can't be resumed.")
        health = self._health_checker.current(project_id)
        if health.published is not None and health.published.error is not None:
            raise ServiceError(
                f"Project '{project_id}' can't be resumed — its published revision no longer builds: "
                f"{health.published.error}",
                status_code=HTTPStatus.CONFLICT, code="project_broken",
            )
        self._db.set_manually_paused(project_id, False)
        self.recompute(project_id)
        return self.get_project_runtime_status(project_id)

    def get_project_runtime_status(self, project_id: str) -> dict:
        is_paused, paused_reason = self._db.get_project_availability(project_id) or (False, None)
        manually_paused = self._db.get_manually_paused(project_id) or False
        return {
            "id": project_id,
            "status": self.project_status(is_paused, manually_paused),
            "paused_reason": paused_reason,
            "revision": self._db.get_project_revision(project_id),
            "published_revision": self._db.get_project_published_revision(project_id),
            **self._health_fields(project_id),
        }

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        return self._db.get_project_availability(project_id) or (False, None)
