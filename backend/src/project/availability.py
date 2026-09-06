from __future__ import annotations

from http import HTTPStatus

from automaton.automaton import Automaton
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from db import Db
from events import AvailabilityChanged, ProjectPublishedHealthChanged, ProjectRevisionBuildFailed, publish, subscribe
from logging_factory import LoggerFactory
from service_error import ServiceError

from .health import ProjectHealth, ProjectHealthChecker
from .archive.automaton_loader import AutomatonLoader

logger = LoggerFactory.get_logger(__name__)


class ProjectAvailability:
    def __init__(self, db: Db, health_checker: ProjectHealthChecker, automaton_loader: AutomatonLoader) -> None:
        self._db = db
        self._health_checker = health_checker
        self._automaton_loader = automaton_loader
        self._recomputing: set[str] = set()

    @staticmethod
    def automaton_project_refs(automaton: Automaton) -> set[str]:
        refs: set[str] = set()
        for state in automaton.states.values():
            for action in state.actions:
                if action.trigger and action.target == state.key:
                    refs |= TriggerExpressionAnalyzer.automaton_project_refs(action.trigger)
        return refs

    def filter_resolvable_project_ids(self, project_ids: set[str]) -> set[str]:
        return {project_id for project_id in project_ids if self._db.project_exists(project_id)}

    def _dependency_unavailable(self, dep_id: str) -> tuple[bool, str]:
        if not self._db.project_exists(dep_id):
            return True, dep_id
        is_paused, _ = self._db.get_project_availability(dep_id) or (True, None)
        return is_paused, dep_id

    def recompute(self, project_id: str) -> None:
        self._recomputing.add(project_id)
        try:
            previous_health = self._health_checker.last_checked(project_id)
            health = self._health_checker.check(project_id)
            self._notify_published_health_change(project_id, previous_health, health)

            if self._db.get_manually_paused(project_id):
                available, reason = False, "Manually paused."
            elif health.published is not None and health.published.error is not None:
                available, reason = False, health.published.error
            else:
                available, reason = True, None
                for dep_id in self._db.get_observed_projects(project_id):
                    is_unavailable, label = self._dependency_unavailable(dep_id)
                    if is_unavailable:
                        available, reason = False, f"Depends on unavailable project '{label}'."
                        break

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

    def _notify_published_health_change(
        self, project_id: str, previous: ProjectHealth | None, current: ProjectHealth,
    ) -> None:
        was_broken = previous is not None and previous.published is not None and previous.published.error is not None
        is_broken = current.published is not None and current.published.error is not None
        if is_broken == was_broken:
            return
        if current.published is not None:
            revision, error = current.published.revision, current.published.error
        else:
            revision, error = self._db.get_project_revision(project_id), None
        publish(ProjectPublishedHealthChanged(project_id=project_id, revision=revision, error=error))

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
            raise ServiceError(health.draft.error, status_code=HTTPStatus.CONFLICT, code="project_broken")

    def recheck_dependents_of_changed_id(
        self, project_id: str, old_project_id: str, new_project_id: str | None,
    ) -> None:
        self._automaton_loader.clear_all_build_failures()
        affected: set[str] = set(self._db.get_observers(old_project_id))

        if new_project_id is not None:
            for other_id in self._db.list_projects():
                if other_id == project_id:
                    continue
                try:
                    other_automaton = self._automaton_loader.load(other_id)
                except Exception:  # noqa: BLE001
                    continue
                other_refs = self.automaton_project_refs(other_automaton)
                if new_project_id not in other_refs:
                    continue
                self._db.set_project_observers(other_id, self.filter_resolvable_project_ids(other_refs))
                affected.add(other_id)

        affected.discard(project_id)
        for observer in affected:
            self.recompute(observer)

    def register_cascade(self) -> None:
        subscribe(AvailabilityChanged, self._on_availability_changed)
        subscribe(ProjectRevisionBuildFailed, self._on_revision_build_failed)

    def _on_availability_changed(self, event: AvailabilityChanged) -> None:
        try:
            for observer in self._db.get_observers(event.project_id):
                self.recompute(observer)
        except Exception:
            logger.exception(
                "Availability cascade failed while reacting to '%s' (available=%s).",
                event.project_id, event.available,
            )

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
        rows = []
        for row in self._db.list_projects_runtime_status():
            health = self._health_checker.current(row["id"])
            rows.append({
                "id": row["id"],
                "status": self.project_status(row["is_paused"], row["manually_paused"]),
                "paused_reason": row["paused_reason"],
                "revision": row["revision"],
                "published_revision": row["published_revision"],
                "broken": {
                    "published": health.published.error if health.published is not None else None,
                    "draft": health.draft.error,
                },
                "build_warnings": health.draft.warnings,
            })
        return rows

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
        }

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        return self._db.get_project_availability(project_id) or (False, None)
