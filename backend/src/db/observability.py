from __future__ import annotations

from .instrumentation import instrument_queries, write
from .models import SystemWarning
from .utils import _utc_iso


@instrument_queries
class ObservabilityMixin:

    @write
    def save_system_warning(
        self, username: str, project_id: str, kind: str, message: str, *,
        file: str | None = None, line: int | None = None,
    ) -> int:
        row = SystemWarning.create(
            user_id=username, project_id=project_id, kind=kind, message=message, file=file, line=line,
        )
        return row.id

    @write
    def delete_system_warning(self, username: str, warning_id: int) -> bool:
        deleted = SystemWarning.delete().where(
            (SystemWarning.id == warning_id) & (SystemWarning.user_id == username)
        ).execute()
        return deleted > 0

    @write
    def delete_project_system_warnings(self, project_id: str, kind: str) -> int:
        return SystemWarning.delete().where(
            (SystemWarning.project_id == project_id) & (SystemWarning.kind == kind)
        ).execute()

    @write
    def delete_system_warnings_of_kind(self, kind: str) -> int:
        """Every project's, for a kind nothing raises any more. A warning
        outlives the run that raised it, so a kind that is withdrawn has
        to be swept once or it stays on somebody's screen forever."""
        return SystemWarning.delete().where(SystemWarning.kind == kind).execute()

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

    def list_system_warnings_for_user(self, username: str, kind: str | None = None, limit: int = 50) -> list[dict]:
        """`username`'s own SystemWarning rows across every project, most
        recent first — Manage projects' own "broken project" warnings
        list (see project/health_notifications.py, the only writer of
        kind="project_broken"). A durable audit trail: a row here outlives
        the project actually being fixed, unlike get_runtime_status's own
        live `broken` field."""
        query = SystemWarning.select().where(SystemWarning.user_id == username)
        if kind is not None:
            query = query.where(SystemWarning.kind == kind)
        rows = query.order_by(SystemWarning.timestamp.desc()).limit(limit)
        return [
            {
                "id": row.id, "project_id": row.project_id, "kind": row.kind, "message": row.message,
                "file": row.file, "line": row.line, "timestamp": _utc_iso(row.timestamp),
            }
            for row in rows
        ]
