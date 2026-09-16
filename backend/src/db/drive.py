from __future__ import annotations

import os
from datetime import datetime

from .models import Drive, database
from .utils import _utc_iso


class DriveMixin:

    def read_drive_file(self, project_id: str, user_id: str, path: str) -> tuple[bytes, str] | None:
        row = Drive.get_or_none(
            (Drive.project == project_id) & (Drive.user == user_id) & (Drive.path == path)
        )
        return (row.content, row.content_type) if row is not None else None

    def write_drive_file(
        self, project_id: str, user_id: str, path: str, content: bytes, content_type: str,
        session_id: int | None = None,
    ) -> None:
        with database.atomic():
            written = Drive.update(
                content=content, content_type=content_type, size=len(content),
                session=session_id, updated_at=datetime.utcnow(),
            ).where(
                (Drive.project == project_id) & (Drive.user == user_id) & (Drive.path == path)
            ).execute()
            if not written:
                Drive.create(
                    project=project_id, user=user_id, path=path, session=session_id,
                    content=content, content_type=content_type, size=len(content),
                )

    def list_drive_files(self, project_id: str, user_id: str | None = None, prefix: str = "") -> list[dict]:
        query = Drive.select(
            Drive.user, Drive.path, Drive.content_type, Drive.size, Drive.updated_at,
        ).where(Drive.project == project_id)
        if user_id is not None:
            query = query.where(Drive.user == user_id)
        if prefix:
            query = query.where(Drive.path.startswith(prefix))
        return [
            {
                "user": row.user_id, "path": row.path, "content_type": row.content_type,
                "size": row.size, "updated_at": _utc_iso(row.updated_at),
            }
            for row in query.order_by(Drive.user, Drive.path)
        ]

    def delete_drive_file(self, project_id: str, user_id: str, path: str) -> bool:
        return bool(Drive.delete().where(
            (Drive.project == project_id) & (Drive.user == user_id) & (Drive.path == path)
        ).execute())

    def delete_drive_files(self, project_id: str, prefix: str = "") -> int:
        query = Drive.delete().where(Drive.project == project_id)
        if prefix:
            query = query.where(Drive.path.startswith(prefix))
        return query.execute()

    @staticmethod
    def _renamed_for_merge(path: str, taken: set[str]) -> str:
        root, ext = os.path.splitext(path)
        candidate, n = f"{root} (merged){ext}", 2
        while candidate in taken:
            candidate = f"{root} (merged {n}){ext}"
            n += 1
        return candidate

    def reassign_drive_files(self, absorbed_id: str, target_id: str) -> None:
        with database.atomic():
            taken: dict[str, set[str]] = {}
            for row in Drive.select(Drive.project, Drive.path).where(Drive.user == target_id):
                taken.setdefault(row.project_id, set()).add(row.path)
            for row in list(Drive.select().where(Drive.user == absorbed_id)):
                project_paths = taken.setdefault(row.project_id, set())
                path = self._renamed_for_merge(row.path, project_paths) if row.path in project_paths else row.path
                project_paths.add(path)
                Drive.update(user=target_id, path=path).where(Drive.id == row.id).execute()
