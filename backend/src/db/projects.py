from __future__ import annotations

from .models import Archive, ChatSession, History, Message, Project, StateRemap, Tracking, database


class ProjectMixin:

    def ensure_project(self, project_name: str) -> None:
        Project.get_or_create(name=project_name, defaults={'revision': 0, 'published_revision': None})

    def project_exists(self, project_name: str) -> bool:
        return Project.get_or_none(Project.name == project_name) is not None

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None] | None:
        """(is_paused, paused_reason) — None if `project_name` doesn't
        exist at all (see ProjectService.recompute_availability, which
        treats that as nothing left to update). A cheap single-row read,
        never a full automaton build — see Project.is_paused's own
        docstring on why a dependent project's own availability is
        always just this, not a re-check of that dependency's own
        content."""
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
        project that doesn't exist at all (see ProjectService.
        get_identifier_registry: a project with no declared project_id is
        never offered as an automaton.* completion target)."""
        project = Project.get_or_none(Project.name == project_name)
        return project.project_id if project is not None else None

    def get_project_name_by_project_id(self, project_id: str) -> str | None:
        """The one translation boundary between project_id (what an
        automaton.* reference names — see automaton.trigger_automaton_
        project_refs) and project_name (what every other table/lookup in
        this schema actually keys on) — see ProjectService's own
        _resolve_automaton_project_refs (build-time, populating the
        reverse index) and tracking.automaton_namespace's own
        AutomatonNamespace (runtime resolution), its only two callers.
        None both when no project declares this id at all, and when
        `project_id` itself is falsy — a dangling/absent reference is
        never an error here, only ever a "nothing to resolve to"."""
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

    def reset_project_for_user(self, username: str, project_name: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where((ChatSession.username == username) & (ChatSession.project_name == project_name)).execute()

    def reset_all(self) -> None:
        Tracking.delete().execute()
        Message.delete().execute()
        ChatSession.delete().execute()

    def _current_revision(self, project_name: str) -> int:
        project = Project.get_or_none(Project.name == project_name)
        return project.revision if project is not None else 0

    def get_project_revision(self, project_name: str) -> int:
        return self._current_revision(project_name)

    def get_project_published_revision(self, project_name: str) -> int | None:
        project = Project.get_or_none(Project.name == project_name)
        return project.published_revision if project is not None else None

    def _ensure_draft_revision(self, project_name: str) -> int:
        """The revision an Archive write/delete must target — forking
        first, inside one transaction, if the current draft is exactly
        the published one (the first edit after a publish): every row of
        `Project.revision` is copied to `Project.revision + 1`, which then
        becomes the new draft. A published revision's own rows are never
        touched again after this point — see Meta.indexes on Archive
        itself (project_name, archive_name, revision) being the unique
        key now, not just (project_name, archive_name)."""
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
            # Every user's own Undo/Redo stack for this project just went
            # stale — it referenced content belonging to the revision that
            # was just frozen, not the new draft (see the resolved design
            # question: clear on fork, rather than tag each entry with the
            # revision it belongs to).
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
        own status icon and display label (see ProjectService.
        list_projects, the one caller — ui_label is None whenever the
        project's own `project:` section never declared one, and the
        frontend falls back to the raw name in that case, same
        convention as every other declared-ui-label field in this app).
        Plain list_projects above stays name-only: every *other* caller
        just needs an existence/membership check, never these extra
        columns."""
        return [
            {"name": p.name, "is_paused": p.is_paused, "ui_label": p.ui_label}
            for p in Project.select(Project.name, Project.is_paused, Project.ui_label)
        ]

    def list_projects_runtime_status(self) -> list[dict]:
        """One row per project — revision, published_revision, is_paused,
        paused_reason, manually_paused — for the Settings > Runtime
        status view (see ProjectService.get_runtime_status, the one
        caller). Every column this needs already lives on Project itself,
        so this is a plain single-table select, no automaton build
        involved."""
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
        """The only caller is ProjectService.delete_project — deletes the
        project entirely, every revision at once (unlike delete_archive,
        this never goes through _ensure_draft_revision: there's no draft
        to protect, the whole project is going away). The Project row
        itself goes with it (list_projects() now reads from Project, not
        from Archive's own distinct project names, so leaving the row
        behind would make a deleted project keep showing up as one with
        zero files)."""
        Archive.delete().where(Archive.project_name == project_name).execute()
        History.delete().where(History.project_name == project_name).execute()
        StateRemap.delete().where(StateRemap.project_name == project_name).execute()
        Project.delete().where(Project.name == project_name).execute()

    def delete_draft_test_sessions(self, project_name: str) -> None:
        """Deletes every 'test' session (EditProjectView.vue's own
        embedded Test chat, see ChatSessionManager.create_draft_session)
        of `project_name` — called by publish_project/revert_to_published,
        the two moments a Test session's own anchored revision stops
        meaning anything (see ChatSessionManager.
        get_or_create_current_draft_session's own "an existing active
        session is reused as-is, whatever revision it carried"
        docstring): reopening Test after either must never resume a
        session still pointing at a draft that no longer exists in that
        shape. A plain revision-number comparison can't replace this —
        revert_to_published resets Project.revision back to
        published_revision's own value, so the number can coincide again
        with an old Test session's own project_revision by pure
        coincidence, even though the content has since changed. Message/
        Tracking rows cascade via their own on_delete='CASCADE' FK (see
        models.py) — no separate delete needed for those. A no-op, not an
        error, when there's nothing to delete."""
        ChatSession.delete().where(
            (ChatSession.project_name == project_name) & (ChatSession.source == 'test')
        ).execute()

    def publish_project(self, project_name: str) -> None:
        """Sets published_revision = revision — a no-op (not an error) if
        they already match, so a concurrent double-click is harmless. Note
        the explicit is_null() branch: published_revision IS NULL right up
        until a project's first publish, and SQL's NULL != revision is
        NULL (neither true nor false) under three-valued logic, not a
        match — a plain != would silently skip that very first publish.
        History is cleared right alongside a real publish (guarded on
        `changed` so a no-op double-click never disrupts anyone's
        in-progress undo stack for nothing) — Archive rows for this
        revision don't fork until the *next* edit (see _ensure_draft_
        revision's own docstring), so undo/redo would otherwise still work
        past a publish, letting it silently rewrite content that's now
        live. delete_draft_test_sessions runs unconditionally (not gated
        on `changed`) — see its own docstring on why a stale Test session
        must never survive a publish, even a no-op one a caller mistakenly
        double-fires."""
        with database.atomic():
            changed = Project.update(published_revision=Project.revision).where(
                (Project.name == project_name)
                & (Project.published_revision.is_null() | (Project.published_revision != Project.revision))
            ).execute()
            if changed:
                History.delete().where(History.project_name == project_name).execute()
            self.delete_draft_test_sessions(project_name)

    def revert_to_published(self, project_name: str) -> None:
        """Discards the entire in-progress draft at once — the current
        draft revision's own Archive rows (created by _ensure_draft_
        revision's own fork-on-first-edit copy, see its own docstring) are
        simply deleted, leaving Project.revision pointed back at
        published_revision's own row set, never touched since the fork
        happened. A no-op (not an error) when there's nothing to revert —
        no prior publication at all, or the draft already *is* the
        published one — same "safe against a stale/duplicate click"
        convention as publish_project. History (now describing edits to a
        revision that no longer exists) is cleared right alongside it,
        same as a fork's own History wipe — delete_draft_test_sessions
        too, for the same reason (see its own docstring)."""
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
