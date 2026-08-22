from __future__ import annotations

from .models import Archive, ChatSession, History, Message, Project, StateRemap, Tracking, database


class ProjectMixin:

    def ensure_project(self, project_name: str) -> None:
        Project.get_or_create(name=project_name, defaults={'revision': 0, 'published_revision': None})

    def project_exists(self, project_name: str) -> bool:
        return Project.get_or_none(Project.name == project_name) is not None

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None] | None:
        """(is_paused, paused_reason) — None if `project_name` doesn't
        exist at all. A cheap single-row read, never a full automaton build."""
        project = Project.get_or_none(Project.name == project_name)
        return (project.is_paused, project.paused_reason) if project is not None else None

    def set_project_availability(self, project_name: str, is_paused: bool, paused_reason: str | None) -> None:
        Project.update(is_paused=is_paused, paused_reason=paused_reason).where(Project.name == project_name).execute()

    def get_manually_paused(self, project_name: str) -> bool | None:
        """None if `project_name` doesn't exist at all — same convention
        as get_project_availability."""
        project = Project.get_or_none(Project.name == project_name)
        return project.manually_paused if project is not None else None

    def set_manually_paused(self, project_name: str, value: bool) -> None:
        Project.update(manually_paused=value).where(Project.name == project_name).execute()

    def get_project_id(self, project_name: str) -> str | None:
        """The other direction of get_project_name_by_project_id below —
        None both for a project that never declared one and for a
        project that doesn't exist at all."""
        project = Project.get_or_none(Project.name == project_name)
        return project.project_id if project is not None else None

    def get_project_name_by_project_id(self, project_id: str) -> str | None:
        """The one translation boundary between project_id (what an
        automaton.* reference names) and project_name (what every other
        table keys on). None when unresolved — never an error."""
        if not project_id:
            return None
        project = Project.get_or_none(Project.project_id == project_id)
        return project.name if project is not None else None

    def set_project_metadata(
        self, project_name: str, project_id: str | None, ui_label: str | None, ui_description: str | None,
    ) -> None:
        Project.update(
            project_id=project_id, ui_label=ui_label, ui_description=ui_description,
        ).where(Project.name == project_name).execute()

    def reset_project(self, project_name: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.project_name == project_name)
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.project_name == project_name).execute()

    def reset_project_for_user(self, username: str, project_name: str, type: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name) & (ChatSession.type == type)
        )
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name) & (ChatSession.type == type)
        ).execute()

    def wipe_live_sessions_for_project(self, project_name: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(
            (ChatSession.project_name == project_name) & (ChatSession.type == 'live')
        )
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(
            (ChatSession.project_name == project_name) & (ChatSession.type == 'live')
        ).execute()

    def _current_revision(self, project_name: str) -> int:
        project = Project.get_or_none(Project.name == project_name)
        return project.revision if project is not None else 0

    def get_project_revision(self, project_name: str) -> int:
        return self._current_revision(project_name)

    def get_project_published_revision(self, project_name: str) -> int | None:
        project = Project.get_or_none(Project.name == project_name)
        return project.published_revision if project is not None else None

    def _ensure_draft_revision(self, project_name: str) -> int:
        """The revision an Archive write/delete must target — forks
        first, in one transaction, if the current draft is exactly the
        published one: every Archive row is copied to revision + 1."""
        with database.atomic():
            project = Project.get(Project.name == project_name)
            if project.revision != project.published_revision:
                return project.revision
            new_revision = project.revision + 1
            for archive in Archive.select().where(
                (Archive.project_name == project_name) & (Archive.revision == project.revision)
            ):
                Archive.create(
                    project_name=project_name, archive_name=archive.archive_name,
                    revision=new_revision, content=archive.content, content_type=archive.content_type,
                )
            Project.update(revision=new_revision).where(Project.name == project_name).execute()
            # Every user's Undo/Redo stack just went stale — it
            # referenced content belonging to the revision just frozen,
            # not the new draft.
            History.delete().where(History.project_name == project_name).execute()
            return new_revision

    def get_archive(self, project_name: str, archive_name: str, revision: int | None = None) -> bytes | None:
        if revision is None:
            revision = self._current_revision(project_name)
        row = Archive.get_or_none(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        )
        return row.content if row is not None else None

    def get_archive_content_type(self, project_name: str, archive_name: str, revision: int | None = None) -> str | None:
        if revision is None:
            revision = self._current_revision(project_name)
        row = Archive.get_or_none(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        )
        return row.content_type if row is not None else None

    def get_archives(self, project_name: str, revision: int | None = None) -> dict:
        if revision is None:
            revision = self._current_revision(project_name)
        return {
            row.archive_name: row.content
            for row in Archive.select(Archive.archive_name, Archive.content).where(
                (Archive.project_name == project_name) & (Archive.revision == revision)
            )
        }

    def save_project_files(self, project_name: str, files: dict[str, bytes], content_types: dict[str, str]) -> None:
        self.ensure_project(project_name)
        revision = self._ensure_draft_revision(project_name)
        for archive_name, content in files.items():
            content_type = content_types[archive_name]
            existing = Archive.get_or_none(
                (Archive.project_name == project_name) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
            )
            if existing is None:
                Archive.create(
                    project_name=project_name, archive_name=archive_name, revision=revision,
                    content=content, content_type=content_type,
                )
            else:
                Archive.update(content=content, content_type=content_type).where(Archive.id == existing.id).execute()

    def list_projects(self) -> list[str]:
        return [p.name for p in Project.select(Project.name)]

    def list_projects_with_availability(self) -> list[dict]:
        """{name, is_paused, ui_label} per project — ProjectsMenu.vue's
        status icon and display label. Plain list_projects above stays
        name-only: every other caller just needs an existence check."""
        return [
            {"name": p.name, "is_paused": p.is_paused, "ui_label": p.ui_label}
            for p in Project.select(Project.name, Project.is_paused, Project.ui_label)
        ]

    def list_projects_runtime_status(self) -> list[dict]:
        """One row per project — revision, published_revision, is_paused,
        paused_reason, manually_paused — for the Settings > Runtime
        status view. A plain single-table select, no automaton build involved."""
        return [
            {
                "name": p.name,
                "revision": p.revision,
                "published_revision": p.published_revision,
                "is_paused": p.is_paused,
                "paused_reason": p.paused_reason,
                "manually_paused": p.manually_paused,
            }
            for p in Project.select(
                Project.name, Project.revision, Project.published_revision,
                Project.is_paused, Project.paused_reason, Project.manually_paused,
            ).order_by(Project.name)
        ]

    def list_archives(self, project_name: str, revision: int | None = None) -> list[str]:
        if revision is None:
            revision = self._current_revision(project_name)
        return [
            p.archive_name for p in Archive.select(Archive.archive_name).where(
                (Archive.project_name == project_name) & (Archive.revision == revision)
            )
        ]

    def delete_archive(self, project_name: str, archive_name: str) -> None:
        revision = self._ensure_draft_revision(project_name)
        Archive.delete().where(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        ).execute()
        History.delete().where((History.project_name == project_name) & (History.archive_name == archive_name)).execute()

    def delete_archives(self, project_name: str) -> None:
        """Deletes the project entirely, every revision at once — unlike
        delete_archive, skips _ensure_draft_revision since the whole
        project is going away. The Project row goes with it too."""
        Archive.delete().where(Archive.project_name == project_name).execute()
        History.delete().where(History.project_name == project_name).execute()
        StateRemap.delete().where(StateRemap.project_name == project_name).execute()
        Project.delete().where(Project.name == project_name).execute()

    def delete_draft_test_sessions(self, project_name: str) -> None:
        """Deletes every 'test' session of `project_name` — called by
        publish_project/revert_to_published, the two moments a Test
        session's anchored revision stops meaning anything."""
        ChatSession.delete().where(
            (ChatSession.project_name == project_name) & (ChatSession.type == 'test')
        ).execute()

    def publish_project(self, project_name: str) -> None:
        """Sets published_revision = revision — a no-op if they already
        match. The explicit is_null() branch matters: SQL's NULL !=
        revision is NULL, not true, so a plain != would skip the first publish."""
        with database.atomic():
            changed = Project.update(published_revision=Project.revision).where(
                (Project.name == project_name)
                & (Project.published_revision.is_null() | (Project.published_revision != Project.revision))
            ).execute()
            if changed:
                History.delete().where(History.project_name == project_name).execute()
            # Runs unconditionally: a stale Test session must never
            # survive even a no-op double-fired publish.
            self.delete_draft_test_sessions(project_name)

    def revert_to_published(self, project_name: str) -> None:
        """Discards the entire in-progress draft — the draft revision's
        Archive rows are deleted, leaving Project.revision pointed back
        at published_revision. A no-op when there's nothing to revert."""
        with database.atomic():
            project = Project.get(Project.name == project_name)
            if project.published_revision is None or project.revision == project.published_revision:
                return
            Archive.delete().where(
                (Archive.project_name == project_name) & (Archive.revision == project.revision)
            ).execute()
            Project.update(revision=project.published_revision).where(Project.name == project_name).execute()
            History.delete().where(History.project_name == project_name).execute()
            self.delete_draft_test_sessions(project_name)

    def get_state_remap(self, project_name: str, old_key: str) -> str | None:
        row = StateRemap.get_or_none((StateRemap.project_name == project_name) & (StateRemap.old_key == old_key))
        return row.new_key if row is not None else None

    def write_state_remap(self, project_name: str, old_key: str, new_key: str) -> None:
        """Flattens every existing row whose own new_key is exactly
        `old_key` onto `new_key` first — so a key remapped across several
        publications always resolves in a single lookup, never a chain."""
        StateRemap.update(new_key=new_key).where(
            (StateRemap.project_name == project_name) & (StateRemap.new_key == old_key)
        ).execute()
        StateRemap.replace(project_name=project_name, old_key=old_key, new_key=new_key).execute()
