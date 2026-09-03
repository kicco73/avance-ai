from __future__ import annotations

from .models import ProjectObserverIndex, SystemWarning


class ObservabilityMixin:
    """SystemWarning (a broken/misconfigured automaton.* reference) and
    ProjectObserverIndex (reverse index of who references a project via
    automaton.*) — grouped since neither is conversation nor project-file data."""

    def save_system_warning(self, username: str, project_id: str, kind: str, message: str) -> int:
        row = SystemWarning.create(user_id=username, project_id=project_id, kind=kind, message=message)
        return row.id

    def get_system_warnings(self, username: str, project_id: str) -> list[dict]:
        rows = (
            SystemWarning.select()
            .where((SystemWarning.user_id == username) & (SystemWarning.project_id == project_id))
            .order_by(SystemWarning.timestamp.asc())
        )
        return [
            {"id": row.id, "kind": row.kind, "message": row.message, "timestamp": row.timestamp}
            for row in rows
        ]

    def set_project_observers(self, observer_project_id: str, observed_project_ids: set[str]) -> None:
        """Replaces every row this project contributes to the index with
        a fresh one per id in `observed_project_ids` — recomputed from
        scratch on every build rather than diffed."""
        ProjectObserverIndex.delete().where(
            ProjectObserverIndex.observer_project_id == observer_project_id
        ).execute()
        for project_id in observed_project_ids:
            ProjectObserverIndex.create(project_id=project_id, observer_project_id=observer_project_id)

    def get_observers(self, project_id: str) -> list[str]:
        """Every project that references `project_id` via automaton.*
        in one of its self-loop triggers — the wake-up handler's "who
        might care that this project just changed" list."""
        rows = ProjectObserverIndex.select().where(ProjectObserverIndex.project_id == project_id)
        return [row.observer_project_id for row in rows]

    def get_observed_projects(self, observer_project_id: str) -> list[str]:
        """The reverse direction of get_observers — every project id
        `observer_project_id` itself depends on via automaton.*."""
        rows = ProjectObserverIndex.select().where(
            ProjectObserverIndex.observer_project_id == observer_project_id
        )
        return [row.project_id for row in rows]
