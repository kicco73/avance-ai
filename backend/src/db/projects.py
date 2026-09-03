from __future__ import annotations

from .models import (
    Archive, ChatSession, EditHistory, Invite, Message, Project, ProjectObserverIndex, StateRemap,
    SystemWarning, Test, TestAggregateResult, Tracking, User, UserProject, database,
)


class ProjectMixin:

    def ensure_project(self, project_id: str) -> None:
        Project.get_or_create(id=project_id, defaults={'revision': 0, 'published_revision': None})

    def rename_project_id(self, old_id: str, new_id: str) -> None:
        """A project's own project.id changed (edited through the "Edit
        project" form, see ProjectManager.finalize_update) — this is now
        a real primary-key rename, cascaded here by hand (SQLite enforces
        `PRAGMA foreign_keys` on UPDATE too, so the FK-backed tables'
        column must move together with Project.id in one transaction,
        foreign_keys off for its duration — same technique
        SchemaMigrator.migrate_legacy_project_identity uses for the
        one-off historical merge, just live and single-project here)."""
        database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            with database.atomic():
                Project.update(id=new_id).where(Project.id == old_id).execute()
                ChatSession.update(project=new_id).where(ChatSession.project == old_id).execute()
                Archive.update(project=new_id).where(Archive.project == old_id).execute()
                Invite.update(project=new_id).where(Invite.project == old_id).execute()
                UserProject.update(project=new_id).where(UserProject.project == old_id).execute()
                User.update(active_project=new_id).where(User.active_project == old_id).execute()
                StateRemap.update(project_id=new_id).where(StateRemap.project_id == old_id).execute()
                Test.update(project_id=new_id).where(Test.project_id == old_id).execute()
                TestAggregateResult.update(project_id=new_id).where(TestAggregateResult.project_id == old_id).execute()
                SystemWarning.update(project_id=new_id).where(SystemWarning.project_id == old_id).execute()
                EditHistory.update(project_id=new_id).where(EditHistory.project_id == old_id).execute()
                ProjectObserverIndex.update(project_id=new_id).where(ProjectObserverIndex.project_id == old_id).execute()
                ProjectObserverIndex.update(observer_project_id=new_id).where(ProjectObserverIndex.observer_project_id == old_id).execute()
        finally:
            database.execute_sql('PRAGMA foreign_keys = ON')

    def project_exists(self, project_id: str) -> bool:
        return Project.get_or_none(Project.id == project_id) is not None

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None] | None:
        """(is_paused, paused_reason) — None if `project_id` doesn't
        exist at all. A cheap single-row read, never a full automaton build."""
        project = Project.get_or_none(Project.id == project_id)
        return (project.is_paused, project.paused_reason) if project is not None else None

    def set_project_availability(self, project_id: str, is_paused: bool, paused_reason: str | None) -> None:
        Project.update(is_paused=is_paused, paused_reason=paused_reason).where(Project.id == project_id).execute()

    def get_manually_paused(self, project_id: str) -> bool | None:
        """None if `project_id` doesn't exist at all — same convention
        as get_project_availability."""
        project = Project.get_or_none(Project.id == project_id)
        return project.manually_paused if project is not None else None

    def set_manually_paused(self, project_id: str, value: bool) -> None:
        Project.update(manually_paused=value).where(Project.id == project_id).execute()

    def set_project_metadata(self, project_id: str, ui_label: str | None, ui_description: str | None) -> None:
        Project.update(ui_label=ui_label, ui_description=ui_description).where(Project.id == project_id).execute()

    def reset_project(self, project_id: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.project == project_id)
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.project == project_id).execute()

    def reset_project_for_user(self, username: str, project_id: str, type: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(
            (ChatSession.username == username) & (ChatSession.project == project_id) & (ChatSession.type == type)
        )
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(
            (ChatSession.username == username) & (ChatSession.project == project_id) & (ChatSession.type == type)
        ).execute()

    def wipe_live_sessions_for_all_projects(self) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.type == 'live')
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.type == 'live').execute()

    def _current_revision(self, project_id: str) -> int:
        project = Project.get_or_none(Project.id == project_id)
        return project.revision if project is not None else 0

    def get_project_revision(self, project_id: str) -> int:
        return self._current_revision(project_id)

    def get_project_draft_edit_count(self, project_id: str) -> int:
        project = Project.get_or_none(Project.id == project_id)
        return project.draft_edit_count if project is not None else 0

    def get_project_published_revision(self, project_id: str) -> int | None:
        project = Project.get_or_none(Project.id == project_id)
        return project.published_revision if project is not None else None

    def _ensure_draft_revision(self, project_id: str) -> int:
        """The revision an Archive write/delete must target — forks
        first, in one transaction, if the current draft is exactly the
        published one: every Archive row is copied to revision + 1."""
        with database.atomic():
            project = Project.get(Project.id == project_id)
            if project.revision != project.published_revision:
                return project.revision
            new_revision = project.revision + 1
            for archive in Archive.select().where(
                (Archive.project == project_id) & (Archive.revision == project.revision)
            ):
                Archive.create(
                    project=project_id, archive_name=archive.archive_name,
                    revision=new_revision, content=archive.content, content_type=archive.content_type,
                )
            Project.update(revision=new_revision).where(Project.id == project_id).execute()
            # Every user's Undo/Redo stack just went stale — it
            # referenced content belonging to the revision just frozen,
            # not the new draft.
            EditHistory.delete().where(EditHistory.project_id == project_id).execute()
            return new_revision

    def get_archive_row(self, project_id: str, archive_name: str, revision: int | None = None) -> Archive | None:
        if revision is None:
            revision = self._current_revision(project_id)
        return Archive.get_or_none(
            (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        )

    def get_archive(self, project_id: str, archive_name: str, revision: int | None = None) -> bytes | None:
        row = self.get_archive_row(project_id, archive_name, revision=revision)
        return row.content if row is not None else None

    def get_archive_content_type(self, project_id: str, archive_name: str, revision: int | None = None) -> str | None:
        if revision is None:
            revision = self._current_revision(project_id)
        row = Archive.get_or_none(
            (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        )
        return row.content_type if row is not None else None

    def get_archives(self, project_id: str, revision: int | None = None) -> dict:
        if revision is None:
            revision = self._current_revision(project_id)
        return {
            row.archive_name: row.content
            for row in Archive.select(Archive.archive_name, Archive.content).where(
                (Archive.project == project_id) & (Archive.revision == revision)
            )
        }

    def save_project_files(self, project_id: str, files: dict[str, bytes], content_types: dict[str, str]) -> None:
        self.ensure_project(project_id)
        revision = self._ensure_draft_revision(project_id)
        Project.update(draft_edit_count=Project.draft_edit_count + 1).where(Project.id == project_id).execute()
        for archive_name, content in files.items():
            content_type = content_types[archive_name]
            existing = Archive.get_or_none(
                (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
            )
            if existing is None:
                Archive.create(
                    project=project_id, archive_name=archive_name, revision=revision,
                    content=content, content_type=content_type,
                )
            else:
                Archive.update(content=content, content_type=content_type).where(Archive.id == existing.id).execute()

    def overwrite_current_draft_file(self, project_id: str, archive_name: str, content: bytes, content_type: str) -> None:
        """In-place content overwrite of the *current* draft revision's own
        Archive row — unlike save_project_files, never forks a new draft
        (see _ensure_draft_revision): for finalizing the draft that's
        already about to be published (see ProjectManager.publish_project's
        own project.revision stamping), not for a genuinely new edit."""
        revision = self._current_revision(project_id)
        existing = Archive.get_or_none(
            (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        )
        if existing is None:
            Archive.create(project=project_id, archive_name=archive_name, revision=revision, content=content, content_type=content_type)
        else:
            Archive.update(content=content, content_type=content_type).where(Archive.id == existing.id).execute()

    def import_new_revision(
        self, project_id: str, revision: int, files: dict[str, bytes], content_types: dict[str, str],
    ) -> None:
        """Replaces the project's entire current draft with `files`, at
        exactly `revision` (not an increment off the current draft — see
        ProjectManager.put_project: `revision` is instance/upload-derived,
        always > the project's own published_revision, but not
        necessarily current_draft + 1). Used only for a re-upload of an
        already-existing project.id, never for a normal in-editor save."""
        self.ensure_project(project_id)
        with database.atomic():
            Project.update(revision=revision).where(Project.id == project_id).execute()
            Archive.delete().where((Archive.project == project_id) & (Archive.revision == revision)).execute()
            for archive_name, content in files.items():
                Archive.create(
                    project=project_id, archive_name=archive_name, revision=revision,
                    content=content, content_type=content_types[archive_name],
                )
            EditHistory.delete().where(EditHistory.project_id == project_id).execute()

    def list_projects(self) -> list[str]:
        return [p.id for p in Project.select(Project.id)]

    def list_projects_with_availability(self) -> list[dict]:
        """{id, is_paused, ui_label} per project — ProjectsMenu.vue's
        status icon and display label. Plain list_projects above stays
        id-only: every other caller just needs an existence check."""
        return [
            {"id": p.id, "is_paused": p.is_paused, "ui_label": p.ui_label}
            for p in Project.select(Project.id, Project.is_paused, Project.ui_label)
        ]

    def list_projects_with_availability_for_user(self, username: str) -> list[dict]:
        """Same shape as list_projects_with_availability above, restricted
        to the projects `username` has a UserProject row for — a plain
        'user' role's own view of ProjectsMenu.vue."""
        return [
            {"id": p.id, "is_paused": p.is_paused, "ui_label": p.ui_label}
            for p in (
                Project.select(Project.id, Project.is_paused, Project.ui_label)
                .join(UserProject, on=(UserProject.project == Project.id))
                .where(UserProject.user == username)
            )
        ]

    def list_projects_runtime_status(self) -> list[dict]:
        """One row per project — revision, published_revision, is_paused,
        paused_reason, manually_paused — for the Settings > Runtime
        status view. A plain single-table select, no automaton build involved."""
        return [
            {
                "id": p.id,
                "revision": p.revision,
                "published_revision": p.published_revision,
                "is_paused": p.is_paused,
                "paused_reason": p.paused_reason,
                "manually_paused": p.manually_paused,
            }
            for p in Project.select(
                Project.id, Project.revision, Project.published_revision,
                Project.is_paused, Project.paused_reason, Project.manually_paused,
            ).order_by(Project.id)
        ]

    def list_distinct_archive_names(self) -> list[tuple[str, str]]:
        return [
            (row.project_id, row.archive_name)
            for row in Archive.select(Archive.project, Archive.archive_name).distinct()
        ]

    def rename_archive_everywhere(self, project_id: str, old_name: str, new_name: str) -> None:
        Archive.update(archive_name=new_name).where(
            (Archive.project == project_id) & (Archive.archive_name == old_name)
        ).execute()

    def list_archives(self, project_id: str, revision: int | None = None) -> list[str]:
        if revision is None:
            revision = self._current_revision(project_id)
        return [
            p.archive_name for p in Archive.select(Archive.archive_name).where(
                (Archive.project == project_id) & (Archive.revision == revision)
            )
        ]

    def delete_archive(self, project_id: str, archive_name: str) -> None:
        revision = self._ensure_draft_revision(project_id)
        Archive.delete().where(
            (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
        ).execute()
        EditHistory.delete().where((EditHistory.project_id == project_id) & (EditHistory.archive_name == archive_name)).execute()

    def delete_archives(self, project_id: str) -> None:
        """Deletes the project entirely, every revision at once — unlike
        delete_archive, skips _ensure_draft_revision since the whole
        project is going away. The Project row goes with it too."""
        Archive.delete().where(Archive.project == project_id).execute()
        EditHistory.delete().where(EditHistory.project_id == project_id).execute()
        StateRemap.delete().where(StateRemap.project_id == project_id).execute()
        Project.delete().where(Project.id == project_id).execute()

    def delete_draft_test_sessions(self, project_id: str) -> None:
        """Deletes every unlabeled 'test' session of `project_id` —
        called by publish_project/revert_to_published, the two moments an
        ephemeral test session's anchored revision stops meaning anything.
        A labeled one is excluded: labeling freezes it as durable benchmark
        ground truth (same contract as a labeled 'live' session, see
        TestService._resolve_scope's own `if row['labeled']` gate) — and
        Test.session cascades on delete, so wiping it here would silently
        destroy every TestReplayJob result ever computed against it too."""
        ChatSession.delete().where(
            (ChatSession.project == project_id) & (ChatSession.type == 'test')
            & (ChatSession.labeled == False)
        ).execute()

    def publish_project(self, project_id: str) -> None:
        """Sets published_revision = revision — a no-op if they already
        match. The explicit is_null() branch matters: SQL's NULL !=
        revision is NULL, not true, so a plain != would skip the first publish."""
        with database.atomic():
            changed = Project.update(published_revision=Project.revision).where(
                (Project.id == project_id)
                & (Project.published_revision.is_null() | (Project.published_revision != Project.revision))
            ).execute()
            if changed:
                EditHistory.delete().where(EditHistory.project_id == project_id).execute()
            # Runs unconditionally: a stale Test session must never
            # survive even a no-op double-fired publish.
            self.delete_draft_test_sessions(project_id)

    def revert_to_published(self, project_id: str) -> None:
        """Discards the entire in-progress draft — the draft revision's
        Archive rows are deleted, leaving Project.revision pointed back
        at published_revision. A no-op when there's nothing to revert."""
        with database.atomic():
            project = Project.get(Project.id == project_id)
            if project.published_revision is None or project.revision == project.published_revision:
                return
            Archive.delete().where(
                (Archive.project == project_id) & (Archive.revision == project.revision)
            ).execute()
            Project.update(revision=project.published_revision).where(Project.id == project_id).execute()
            EditHistory.delete().where(EditHistory.project_id == project_id).execute()
            self.delete_draft_test_sessions(project_id)

    def get_state_remap(self, project_id: str, old_key: str) -> str | None:
        row = StateRemap.get_or_none((StateRemap.project_id == project_id) & (StateRemap.old_key == old_key))
        return row.new_key if row is not None else None

    def write_state_remap(self, project_id: str, old_key: str, new_key: str) -> None:
        """Flattens every existing row whose own new_key is exactly
        `old_key` onto `new_key` first — so a key remapped across several
        publications always resolves in a single lookup, never a chain."""
        StateRemap.update(new_key=new_key).where(
            (StateRemap.project_id == project_id) & (StateRemap.new_key == old_key)
        ).execute()
        StateRemap.replace(project_id=project_id, old_key=old_key, new_key=new_key).execute()
