from __future__ import annotations

from .models import EditHistory
from peewee import fn


class HistoryMixin:

    def _next_history_seq(self, user_id: str, project_name: str, archive_name: str, kind: str) -> int:
        latest = EditHistory.select(fn.MAX(EditHistory.seq)).where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).scalar()
        return 0 if latest is None else latest + 1

    def _push_history(self, user_id: str, project_name: str, archive_name: str, kind: str, content: bytes) -> None:
        seq = self._next_history_seq(user_id, project_name, archive_name, kind)
        EditHistory.create(user_id=user_id, project_name=project_name, archive_name=archive_name, kind=kind, seq=seq, content=content)

    def _pop_history(self, user_id: str, project_name: str, archive_name: str, kind: str) -> bytes | None:
        row = EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).order_by(EditHistory.seq.desc()).first()
        if row is None:
            return None
        content = row.content
        row.delete_instance()
        return content

    def _clear_history_kind(self, user_id: str, project_name: str, archive_name: str, kind: str) -> None:
        EditHistory.delete().where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).execute()

    def has_undo(self, user_id: str, project_name: str, archive_name: str) -> bool:
        return EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == 'undo')).exists()

    def has_redo(self, user_id: str, project_name: str, archive_name: str) -> bool:
        return EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == 'redo')).exists()

    def save_project_file(self, user_id: str, project_name: str, archive_name: str, content: bytes, content_type: str) -> None:
        self.ensure_project(project_name)
        # Resolve the fork before touching EditHistory at all —
        # _ensure_draft_revision wipes every user's EditHistory rows on a
        # fork, which would otherwise erase the undo entry pushed below.
        self._ensure_draft_revision(project_name)
        previous = self.get_archive(project_name, archive_name)
        if previous is not None:
            self._push_history(user_id, project_name, archive_name, 'undo', previous)
        self._clear_history_kind(user_id, project_name, archive_name, 'redo')
        self.save_project_files(project_name, {archive_name: content}, {archive_name: content_type})

    def undo_project_file(self, user_id: str, project_name: str, archive_name: str, current_content: bytes) -> bytes | None:
        previous = self._pop_history(user_id, project_name, archive_name, 'undo')
        if previous is None:
            return None
        self._push_history(user_id, project_name, archive_name, 'redo', current_content)
        return previous

    def redo_project_file(self, user_id: str, project_name: str, archive_name: str, current_content: bytes) -> bytes | None:
        next_content = self._pop_history(user_id, project_name, archive_name, 'redo')
        if next_content is None:
            return None
        self._push_history(user_id, project_name, archive_name, 'undo', current_content)
        return next_content

    def clear_history(self, user_id: str, project_name: str) -> None:
        EditHistory.delete().where((EditHistory.user_id == user_id) & (EditHistory.project_name == project_name)).execute()
