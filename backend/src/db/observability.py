from __future__ import annotations

from .models import ProjectObserverIndex, SystemWarning


class ObservabilityMixin:
    """SystemWarning (a broken/misconfigured automaton.* reference) and
    ProjectObserverIndex (reverse index of who references a project via
    automaton.*) — grouped since neither is conversation nor project-file data."""

    def save_system_warning(self, username: str, project_name: str, kind: str, message: str) -> int:
        row = SystemWarning.create(username=username, project_name=project_name, kind=kind, message=message)
        return row.id

    def get_system_warnings(self, username: str, project_name: str) -> list[dict]:
        rows = (
            SystemWarning.select()
            .where((SystemWarning.username == username) & (SystemWarning.project_name == project_name))
            .order_by(SystemWarning.timestamp.asc())
        )
        return [
            {"id": row.id, "kind": row.kind, "message": row.message, "timestamp": row.timestamp}
            for row in rows
        ]

    def set_project_observers(self, observer_project_name: str, observed_project_names: set[str]) -> None:
        """Replaces every row this project contributes to the index with
        a fresh one per name in `observed_project_names` — recomputed
        from scratch on every build rather than diffed."""
        ProjectObserverIndex.delete().where(
            ProjectObserverIndex.observer_project_name == observer_project_name
        ).execute()
        for project_name in observed_project_names:
            ProjectObserverIndex.create(project_name=project_name, observer_project_name=observer_project_name)

    def get_observers(self, project_name: str) -> list[str]:
        """Every project that references `project_name` via automaton.*
        in one of its self-loop triggers — the wake-up handler's "who
        might care that this project just changed" list."""
        rows = ProjectObserverIndex.select().where(ProjectObserverIndex.project_name == project_name)
        return [row.observer_project_name for row in rows]

    def get_observed_projects(self, observer_project_name: str) -> list[str]:
        """The reverse direction of get_observers — every project
        `observer_project_name` itself depends on via automaton.*."""
        rows = ProjectObserverIndex.select().where(
            ProjectObserverIndex.observer_project_name == observer_project_name
        )
        return [row.project_name for row in rows]
