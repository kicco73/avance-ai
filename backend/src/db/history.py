from __future__ import annotations

from dataclasses import dataclass

from .models import EditHistory
from peewee import fn


@dataclass(frozen=True)
class ContentRestored:
    """undo/redo popped an ordinary content-snapshot row — `content` is
    what archive_name should now read."""
    content: bytes


@dataclass(frozen=True)
class FileRenamed:
    """undo/redo popped a rename-marker row instead — the archive itself
    already moved to `active_name`; callers must address any further
    undo/redo (and everything else) at that name from now on."""
    active_name: str


UndoRedoOutcome = ContentRestored | FileRenamed


class HistoryMixin:

    def _next_history_seq(self, user_id: str, project_id: str, archive_name: str, kind: str) -> int:
        latest = EditHistory.select(fn.MAX(EditHistory.seq)).where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).scalar()
        return 0 if latest is None else latest + 1

    def _push_history(self, user_id: str, project_id: str, archive_name: str, kind: str, content: bytes) -> None:
        seq = self._next_history_seq(user_id, project_id, archive_name, kind)
        EditHistory.create(user_id=user_id, project_id=project_id, archive_name=archive_name, kind=kind, seq=seq, content=content)

    def _push_rename_marker(self, user_id: str, project_id: str, archive_name: str, kind: str, rename_target: str) -> None:
        seq = self._next_history_seq(user_id, project_id, archive_name, kind)
        EditHistory.create(user_id=user_id, project_id=project_id, archive_name=archive_name, kind=kind, seq=seq, content=None, rename_target=rename_target)

    def _pop_history(self, user_id: str, project_id: str, archive_name: str, kind: str) -> EditHistory | None:
        row = EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).order_by(EditHistory.seq.desc()).first()
        if row is None:
            return None
        row.delete_instance()
        return row

    def _clear_history_kind(self, user_id: str, project_id: str, archive_name: str, kind: str) -> None:
        EditHistory.delete().where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == kind)).execute()

    def has_undo(self, user_id: str, project_id: str, archive_name: str) -> bool:
        return EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == 'undo')).exists()

    def has_redo(self, user_id: str, project_id: str, archive_name: str) -> bool:
        return EditHistory.select().where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name) & (EditHistory.kind == 'redo')).exists()

    def save_project_file(self, user_id: str, project_id: str, archive_name: str, content: bytes, content_type: str) -> None:
        self.ensure_project(project_id)
        # Resolve the fork before touching EditHistory at all —
        # _ensure_draft_revision wipes every user's EditHistory rows on a
        # fork, which would otherwise erase the undo entry pushed below.
        self._ensure_draft_revision(project_id)
        previous = self.get_archive(project_id, archive_name)
        if previous is not None:
            self._push_history(user_id, project_id, archive_name, 'undo', previous)
        self._clear_history_kind(user_id, project_id, archive_name, 'redo')
        self.save_project_files(project_id, {archive_name: content}, {archive_name: content_type})

    def rename_project_file(
        self, user_id: str, project_id: str, old_name: str, new_name: str,
        updated_files: dict[str, bytes] | None = None, content_types: dict[str, str] | None = None,
    ) -> None:
        """Moves old_name's own undo stack forward onto new_name via a
        single rename-marker entry — old_name's own (pre-rename) stack is
        left completely untouched, dormant until an undo of this marker
        makes old_name the active name again (see undo_project_file/
        redo_project_file below, which move nothing else). `updated_files`
        (index.yml/index.css, if the rename rewrote a reference to
        old_name's own basename) get an ordinary content-undo entry each,
        exactly as save_project_file would push for any other edit of theirs."""
        self.ensure_project(project_id)
        self._ensure_draft_revision(project_id)  # fork (and wipe EditHistory) before touching history below
        updated_files = updated_files or {}
        for archive_name in updated_files:
            previous = self.get_archive(project_id, archive_name)
            if previous is not None:
                self._push_history(user_id, project_id, archive_name, 'undo', previous)
            self._clear_history_kind(user_id, project_id, archive_name, 'redo')
        self._push_rename_marker(user_id, project_id, new_name, 'undo', old_name)
        self._clear_history_kind(user_id, project_id, new_name, 'redo')
        self.rename_archive(project_id, old_name, new_name, updated_files, content_types)

    def undo_project_file(self, user_id: str, project_id: str, archive_name: str, current_content: bytes) -> UndoRedoOutcome | None:
        row = self._pop_history(user_id, project_id, archive_name, 'undo')
        if row is None:
            return None
        if row.rename_target is not None:
            self.rename_archive(project_id, archive_name, row.rename_target)
            self._push_rename_marker(user_id, project_id, row.rename_target, 'redo', archive_name)
            return FileRenamed(active_name=row.rename_target)
        self._push_history(user_id, project_id, archive_name, 'redo', current_content)
        return ContentRestored(content=row.content)

    def redo_project_file(self, user_id: str, project_id: str, archive_name: str, current_content: bytes) -> UndoRedoOutcome | None:
        row = self._pop_history(user_id, project_id, archive_name, 'redo')
        if row is None:
            return None
        if row.rename_target is not None:
            self.rename_archive(project_id, archive_name, row.rename_target)
            self._push_rename_marker(user_id, project_id, row.rename_target, 'undo', archive_name)
            return FileRenamed(active_name=row.rename_target)
        self._push_history(user_id, project_id, archive_name, 'undo', current_content)
        return ContentRestored(content=row.content)

    def clear_history(self, user_id: str, project_id: str) -> None:
        EditHistory.delete().where((EditHistory.user_id == user_id) & (EditHistory.project_id == project_id)).execute()
