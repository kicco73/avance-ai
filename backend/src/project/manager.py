from __future__ import annotations

import io
import json
import tempfile
import zipfile
from http import HTTPStatus
from pathlib import Path
from typing import Mapping

from automaton.automaton import Automaton
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from chat.session_manager import ChatSessionManager
from db import Db
from events import AvailabilityChanged, ProjectPublishedHealthChanged, publish, subscribe
from logging_factory import LoggerFactory
from service_error import ServiceError
from session import Session
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

from .health import ProjectHealth, ProjectHealthChecker
from .inspector import ProjectInspector
from .archive.automaton_loader import AutomatonLoader
from .archive.layout import (
    CACHE_DIR, ArchiveLayout, IMAGE_CONTENT_TYPE_BY_EXTENSION, IMAGE_EXTENSIONS, LEGAL_TERMS_FILE_NAME,
    SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME, TEXT_CONTENT_TYPE_BY_EXTENSION,
)
from .archive.zip_importer import ZipImporter
from .project_import_bundle_job import ProjectImportBundleJob
from .types import CommitCallback

logger = LoggerFactory.get_logger(__name__)

# "New project" starts from this sample zip, resolved off this module's own
# location, not the cwd. Its own declared project.id is only ever a
# starting point — create_new_project always mints a fresh one (see
# _unique_project_id), since every id must be globally unique and the
# template's own id is already taken after the very first use.
NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "projects" / "Hello world.zip"


class ProjectManager:
    def __init__(
        self, db: Db, automaton_loader: AutomatonLoader, inspector: ProjectInspector,
        session_export_manager: SessionExportManager, session_import_manager: SessionImportManager,
        session_manager: ChatSessionManager,
    ) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        self._inspector = inspector
        self._session_export_manager = session_export_manager
        self._session_import_manager = session_import_manager
        self._session_manager = session_manager
        self._health_checker = ProjectHealthChecker(db, automaton_loader)

    @staticmethod
    def _automaton_project_refs(automaton: Automaton) -> set[str]:
        """Every project id `automaton`'s self-loop actions reference via
        automaton.* — raw tokens, not yet checked for existence/family."""
        refs: set[str] = set()
        for state in automaton.states.values():
            for action in state.actions:
                if action.trigger and action.target == state.key:
                    refs |= TriggerExpressionAnalyzer.automaton_project_refs(action.trigger)
        return refs

    def _filter_resolvable_project_ids(self, project_ids: set[str]) -> set[str]:
        """Every id in `project_ids` that currently names a real project. An
        id matching none is silently dropped, not an error — a dangling
        reference is a runtime concern, not build-time (the referenced
        project might simply not exist yet)."""
        return {project_id for project_id in project_ids if self._db.project_exists(project_id)}

    def _dependency_unavailable(self, dep_id: str) -> tuple[bool, str]:
        """(is_unavailable, display_label) for one observed dependency,
        named by id. No Project row left at all (deleted after this
        project came to observe it) counts as unavailable too — the id
        itself stands in as the label since there's nothing else to show."""
        if not self._db.project_exists(dep_id):
            return True, dep_id
        is_paused, _ = self._db.get_project_availability(dep_id) or (True, None)
        return is_paused, dep_id

    def recompute_availability(self, project_id: str) -> None:
        """Available exactly when the published revision builds, no
        manual pause is set, and every automaton.* dependency is itself
        available. Writes only on change — this is what makes it safe to
        call from a cascade with no cycle detection. A broken *draft* with
        a healthy published revision never pauses anything — see
        ensure_project_not_broken for that case's own, separate gate."""
        previous_health = self._health_checker.last_checked(project_id)
        health = self._health_checker.check(project_id)
        self._notify_published_health_change(project_id, previous_health, health)

        if self._db.get_manually_paused(project_id):
            available, reason = False, "Manually paused."
        elif health.published is not None and health.published.error is not None:
            available, reason = False, health.published.error
        else:
            available, reason = True, None
            blocking = None
            for dep_id in self._db.get_observed_projects(project_id):
                is_unavailable, label = self._dependency_unavailable(dep_id)
                if is_unavailable:
                    blocking = label
                    break
            if blocking is not None:
                available, reason = False, f"Depends on unavailable project '{blocking}'."

        current = self._db.get_project_availability(project_id)
        if current is None:
            return  # project no longer exists — nothing left to update
        was_paused, _ = current
        if was_paused == (not available):
            return  # unchanged — see this method's own docstring on why this is the whole guard
        self._db.set_project_availability(project_id, is_paused=not available, paused_reason=reason)
        publish(AvailabilityChanged(project_id=project_id, available=available))

    def _notify_published_health_change(
        self, project_id: str, previous: ProjectHealth | None, current: ProjectHealth,
    ) -> None:
        """Fires ProjectPublishedHealthChanged exactly on a real
        broken<->healthy transition of the *published* revision — never
        repeated while it stays the same, and never for the draft alone
        (see ProjectHealthNotifications, the event's only subscriber)."""
        was_broken = previous is not None and previous.published is not None and previous.published.error is not None
        is_broken = current.published is not None and current.published.error is not None
        if is_broken == was_broken:
            return
        if current.published is not None:
            revision, error = current.published.revision, current.published.error
        else:
            revision, error = self._db.get_project_revision(project_id), None
        publish(ProjectPublishedHealthChanged(project_id=project_id, revision=revision, error=error))

    def recompute_all_availability(self) -> None:
        """Boot-time sweep: every project's own health (published/draft)
        is recomputed from scratch — the in-memory ProjectHealthChecker
        starts out empty on every process start, by design (see its own
        docstring) — and its availability recast accordingly, cascading
        through automaton.* dependencies exactly like any other recompute.
        One project's own unexpected failure here must never take the
        whole boot down with it (same "one broken revision must never
        block the rest" contract as the legacy migrations run just before
        this) — the whole point of this sweep is to turn exactly that
        into a paused project instead."""
        for project_id in self._db.list_projects():
            try:
                self.recompute_availability(project_id)
            except Exception:
                logger.exception(
                    "recompute_availability failed for project '%s' during the boot-time sweep.", project_id
                )

    def ensure_project_not_broken(self, project_id: str) -> None:
        """Raises a 409 ServiceError (code="project_broken") when
        `project_id`'s own *current draft* doesn't build — the one gate
        every automaton-derived design-view endpoint shares (see
        EditProjectController), so a broken project degrades to "files
        only" instead of a stray 400/500 from deep inside the Inspector/
        Editor. Never about is_paused: a broken draft with a healthy
        published revision must never block editing (see
        recompute_availability's own docstring). A no-op for a project
        that doesn't exist at all — that's a 404, same as always, not a
        "broken" one; the caller's own real lookup raises it right after."""
        if not self._db.project_exists(project_id):
            return
        health = self._health_checker.current(project_id)
        if health.draft.error is not None:
            raise ServiceError(health.draft.error, status_code=HTTPStatus.CONFLICT, code="project_broken")

    def _observers_of(self, project_id: str) -> list[str]:
        """Every project observing `project_id` via automaton.* — the
        observer index is keyed by the observed project's own id directly
        now that project identity is unified."""
        return self._db.get_observers(project_id)

    def _recheck_dependents_of_changed_id(
        self, project_id: str, old_project_id: str, new_project_id: str | None,
    ) -> None:
        """`project_id` just stopped answering to `old_project_id` and/or
        started answering to `new_project_id` (a brand new project's id
        counts as "started", `new_project_id` None means it was deleted
        instead of renamed).

        A project observing the *old* id is already correctly indexed —
        that reference just became unresolvable, so a plain recompute is
        enough (same as a delete). A project merely waiting on the *new*
        one was never indexed at all if the id didn't resolve to anything
        until now (see _filter_resolvable_project_ids silently dropping
        it) — those can only be found by re-scanning every project's raw
        triggers directly, and need their own observer-index row
        refreshed before a recompute of theirs means anything."""
        affected: set[str] = set(self._db.get_observers(old_project_id))

        if new_project_id is not None:
            for other_id in self._db.list_projects():
                if other_id == project_id:
                    continue
                try:
                    other_automaton = self._automaton_loader.load(other_id)
                except Exception:  # noqa: BLE001 — a broken build has nothing to refresh
                    continue
                other_refs = self._automaton_project_refs(other_automaton)
                if new_project_id not in other_refs:
                    continue
                self._db.set_project_observers(other_id, self._filter_resolvable_project_ids(other_refs))
                affected.add(other_id)

        affected.discard(project_id)
        for observer in affected:
            self.recompute_availability(observer)

    def register_availability_cascade(self) -> None:
        """Subscribed once, for the process's lifetime. Recursive by
        construction: recompute_availability's write-only-on-change guard
        is what makes the cascade stop propagating on its own."""
        subscribe(AvailabilityChanged, self._on_availability_changed)

    def _on_availability_changed(self, event: AvailabilityChanged) -> None:
        try:
            for observer in self._observers_of(event.project_id):
                self.recompute_availability(observer)
        except Exception:
            logger.exception(
                "Availability cascade failed while reacting to '%s' (available=%s).",
                event.project_id, event.available,
            )

    @staticmethod
    def _project_status(is_paused: bool, manually_paused: bool) -> str:
        """'running' | 'paused' | 'manually_paused'. manually_paused
        always implies is_paused, so checking it first is enough to tell
        the two paused cases apart."""
        if manually_paused:
            return "manually_paused"
        if is_paused:
            return "paused"
        return "running"

    def get_runtime_status(self) -> list[dict]:
        """One row per project for the Settings > Runtime status view.
        `broken` is read-only display info (see ProjectHealthChecker.current)
        — it never feeds is_paused/paused_reason here, and never updates
        the transition memory recompute_availability itself relies on."""
        rows = []
        for row in self._db.list_projects_runtime_status():
            health = self._health_checker.current(row["id"])
            rows.append({
                "id": row["id"],
                "status": self._project_status(row["is_paused"], row["manually_paused"]),
                "paused_reason": row["paused_reason"],
                "revision": row["revision"],
                "published_revision": row["published_revision"],
                "broken": {
                    "published": health.published.error if health.published is not None else None,
                    "draft": health.draft.error,
                },
            })
        return rows

    def accept_legal_terms(self, username: str, project_id: str) -> None:
        """Records that `username` has accepted `project_id`'s current
        legal/terms.md — a no-op if the project has none. Always accepts
        whichever *published* Archive row is current right now, matching
        legal_terms_pending's own revision — never the draft's, which can
        hold a different row id for the same file the moment the project
        has any unpublished edit: if the published terms changed again in
        between, the next legal_terms_pending check will correctly ask again."""
        current = self._db.get_archive_row(
            project_id, LEGAL_TERMS_FILE_NAME, revision=self._inspector.get_published_revision(project_id)
        )
        if current is None:
            return
        self._db.record_terms_acceptance(username, project_id, current.id)

    def set_manually_paused(self, project_id: str) -> dict:
        """Only allowed from 'running', enforced here rather than left to
        the UI alone. Recomputing afterward reuses the normal
        AvailabilityChanged cascade rather than a separate one."""
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_id) or (False, None)
        manually_paused = self._db.get_manually_paused(project_id) or False
        status = self._project_status(is_paused, manually_paused)
        if status != "running":
            raise ValueError(f"Project '{project_id}' isn't running (status: '{status}') — can't be manually paused.")
        self._db.set_manually_paused(project_id, True)
        self.recompute_availability(project_id)
        return self.get_project_runtime_status(project_id)

    def set_manually_running(self, project_id: str) -> dict:
        """Only allowed from 'manually_paused'. Clears the flag and lets
        recompute_availability report the real state again, which may
        still be unavailable if a dependency went down in the meantime."""
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_id) or (False, None)
        manually_paused = self._db.get_manually_paused(project_id) or False
        status = self._project_status(is_paused, manually_paused)
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
        self.recompute_availability(project_id)
        return self.get_project_runtime_status(project_id)

    def get_project_runtime_status(self, project_id: str) -> dict:
        """One row, same shape as get_runtime_status — lets the
        pause/resume endpoints refresh just this row."""
        is_paused, paused_reason = self._db.get_project_availability(project_id) or (False, None)
        manually_paused = self._db.get_manually_paused(project_id) or False
        return {
            "id": project_id,
            "status": self._project_status(is_paused, manually_paused),
            "paused_reason": paused_reason,
            "revision": self._db.get_project_revision(project_id),
            "published_revision": self._db.get_project_published_revision(project_id),
        }

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        """(is_paused, paused_reason). Returns (False, None), never
        raises, for a project that doesn't exist at all."""
        return self._db.get_project_availability(project_id) or (False, None)

    def _project_update_changed(self, existing: Mapping[str, str | bytes], files: Mapping[str, str | bytes]) -> bool:
        """Whether `files` is a genuine change against `existing`. A file
        `existing` doesn't have yet always counts as changed, even with ""
        content — a brand-new empty file must still get persisted."""
        return any(name not in existing or existing[name] != content for name, content in files.items())

    def prepare_update(
        self, project_id: str, files: Mapping[str, str | bytes]
    ) -> tuple[Automaton, dict[str, str | bytes] | None]:
        """Builds+validates the Automaton for `files` merged onto
        `project_id`'s current files. Read-only. Returns (automaton,
        to_persist), where to_persist is None if nothing actually changed."""
        existing = ArchiveLayout.decode_text(self._db.get_archives(project_id))
        merged = {**existing, **files}

        # Peeked ahead of the real build only to seed known_projects_env_keys
        # with the right id/family — relevant only in the rare case this
        # same edit is also changing project.id/family itself (see finalize_update).
        declared_id, declared_family, _ = AutomatonBuilder.read_declared_env_keys(merged["index.yml"])
        try:
            automaton = AutomatonBuilder().build(
                merged, self._automaton_loader.known_projects_env_keys(declared_id or project_id, declared_family)
            )
        except AutomatonBuildError as exc:
            # The one place that knows which draft revision `files` was
            # actually merged onto — build() itself is revision-agnostic,
            # and the frontend needs this to decide whether a build
            # error's own line is still safe to jump to (see CodeEditor.vue).
            exc.project_id = exc.project_id or project_id
            exc.revision = self._db.get_project_revision(project_id)
            raise
        self._validate_project_id_globally_unique(project_id, automaton.project_id)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _validate_project_id_globally_unique(self, project_id: str, new_project_id: str) -> None:
        """The one project.id check AutomatonBuilder can't do itself:
        whether some *other* project has already claimed it. Raises before
        anything gets persisted, so a failed save leaves nothing partial.
        A no-op when the id isn't actually changing."""
        if new_project_id == project_id:
            return
        if self._db.project_exists(new_project_id):
            raise ValueError(
                f"project.id '{new_project_id}' is already used by another project — "
                "project.id must be globally unique."
            )

    async def finalize_update(
        self, project_id: str, automaton: Automaton, commit: CommitCallback, *, is_new_project: bool = False
    ) -> str:
        """Called by every project-mutating path before awaiting `commit`.
        `project_id` is this project's own *current* id; if `automaton`'s
        own just-built project.id differs, that's a live rename request
        (the "Edit project" form's id field) — cascaded across every
        table via Db.rename_project_id before anything else here uses the
        new identity. Returns the *final* project id — every caller must
        use this, not the `project_id` it originally passed in, for
        anything it does afterward (see e.g. ProjectEditor.put_project_file's
        own response, which names the project it just edited). `is_new_project`
        (set only by the upload/create paths, which already had to call
        ensure_project themselves before this) triggers the same dangling-
        reference rescan a rename does: some other project may already
        have an unresolvable automaton.* reference to this id, saved
        before it ever existed. Refreshes the automaton cache and resets
        the active project's live conversation only when its current
        state no longer exists."""
        if automaton.project_id != project_id:
            old_project_id = project_id
            self._db.rename_project_id(old_project_id, automaton.project_id)
            self._automaton_loader.invalidate_cache(old_project_id)
            project_id = automaton.project_id
            self._recheck_dependents_of_changed_id(project_id, old_project_id, project_id)
        elif is_new_project:
            self._recheck_dependents_of_changed_id(project_id, project_id, project_id)

        self._db.set_project_metadata(project_id, automaton.project_ui_label, automaton.project_ui_description)

        revision = self._db.get_project_revision(project_id)
        automaton.set_storage_location(revision)
        self._automaton_loader.set_cached(project_id, revision, automaton)
        # Reverse index of every project this one's self-loop actions
        # reference via automaton.* — recomputed on every successful build
        # regardless of whether `project_id` is currently active.
        observed_project_ids = self._filter_resolvable_project_ids(self._automaton_project_refs(automaton))
        self._db.set_project_observers(project_id, observed_project_ids)
        self.recompute_availability(project_id)
        if project_id == self._inspector.get_active_project_id():
            username = Session().user
            for session_type in ('live', 'test'):
                session = self._session_manager.get_active_session(username, project_id, type=session_type)
                if session is not None and session["end_state"] not in automaton.states:
                    self._db.delete_chat_session(session["id"])
            await commit(project_id, automaton)
        return project_id

    def reset_test_sessions(self, project_id: str) -> None:
        self._db.reset_project_for_user(Session().user, project_id, type='test')

    def wipe_all_live_sessions(self) -> None:
        self._db.wipe_live_sessions_for_all_projects()

    def clean_unused_revisions(self) -> int:
        return self._db.delete_unused_archive_revisions()

    def preview_publish(self, project_id: str) -> dict:
        """Whether publishing `project_id` needs a human state remap
        decision: the current persisted state has gone missing from the
        draft about to be published. Also reports `has_active_sessions`."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        draft = self._automaton_loader.load(project_id)
        current_state_key = self._db.get_current_state(project_id, type='live')
        published_revision = self._db.get_project_published_revision(project_id)
        has_active_sessions = (
            published_revision is not None
            and self._session_manager.has_open_sessions_for_revision(project_id, published_revision)
        )
        if current_state_key is None or current_state_key in draft.states:
            return {"needs_remap": False, "has_active_sessions": has_active_sessions}
        return {
            "needs_remap": True,
            "missing_state": current_state_key,
            "available_states": [state.key for state in draft.states.values() if state.key != ""],
            "has_active_sessions": has_active_sessions,
        }

    def publish_project(self, project_id: str, remap_to: str | None = None) -> dict:
        """Sets published_revision = revision, freezing the current draft —
        and, first, stamps that same number into the draft's own
        project.revision (see PROJECT_SPECS.md §2.1): every publish makes
        the exported index.yml self-describing, which is what a future
        re-upload compares its own project.revision against (see
        put_project). If the persisted state has gone missing from the
        draft, `remap_to` must name a real state; the StateRemap written
        is consulted from then on."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        draft = self._automaton_loader.load(project_id)
        current_state_key = self._db.get_current_state(project_id, type='live')
        if current_state_key is not None and current_state_key not in draft.states:
            if remap_to is None:
                raise ValueError(
                    f"State '{current_state_key}' no longer exists in this revision — a remap target is required."
                )
            if remap_to == "" or remap_to not in draft.states:
                raise ValueError(f"'{remap_to}' is not a valid state in this revision.")
            self._db.write_state_remap(project_id, current_state_key, remap_to)

        new_revision = self._db.get_project_revision(project_id)
        if draft.project_revision != new_revision:
            current_yaml = self._db.get_archive(project_id, "index.yml").decode("utf-8")
            editor = AutomatonYamlEditor(current_yaml)
            editor.set_project_revision(new_revision)
            self._db.overwrite_current_draft_file(project_id, "index.yml", editor.serialize().encode("utf-8"), "text/yaml")
            self._automaton_loader.invalidate_cache(project_id)

        self._db.publish_project(project_id)
        return self._inspector.get_project_revision_info(project_id)

    async def revert_to_published(self, project_id: str, commit: CommitCallback) -> dict:
        """Discards the entire in-progress draft revision, reverting to
        whatever was last published."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        self._db.revert_to_published(project_id)
        self._automaton_loader.invalidate_cache(project_id)
        new_automaton = self._automaton_loader.load(project_id)
        project_id = await self.finalize_update(project_id, new_automaton, commit)
        return self._inspector.get_project_revision_info(project_id)

    async def activate_project(self, project_id: str, commit: CommitCallback) -> Automaton:
        """Validates via _load_and_validate(), persists `project_id` as
        active, then awaits `commit(project_id, new_automaton)`."""
        new_automaton = self._automaton_loader.load(project_id)
        # active_project_id is a real FK onto Project.id — for a brand
        # new project (commit below is what first calls save_project_files/
        # ensure_project), the row doesn't exist yet at this point, so it
        # has to be ensured here first. Idempotent, so a no-op for an
        # already-persisted project.
        self._db.ensure_project(project_id)
        self._db.set_active_project_id(project_id, Session().user)
        await commit(project_id, new_automaton)
        return new_automaton

    async def activate_project_idempotent(self, project_id: str, commit: CommitCallback) -> Automaton:
        """Always validates `project_id` first, even if already active —
        idempotency only skips the swap + commit, never the correctness
        checks. A different project delegates to activate_project()."""
        new_automaton = self._automaton_loader.load(project_id)
        if project_id == self._inspector.get_active_project_id():
            return new_automaton
        return await self.activate_project(project_id, commit)

    def _extract_upload_files(
        self, content: bytes, content_type: str | None,
    ) -> tuple[dict[str, str | bytes], list[dict], list[dict]]:
        """Raw upload body (a zip archive, or a single bare YAML file
        treated as index.yml's own content with no attachments) ->
        (files, bundled sessions.json/tests.json entries). Pure
        extraction — no build/validation, no persistence."""
        if ZipImporter.looks_like_zip(content_type, content):
            with tempfile.TemporaryDirectory() as tmp:
                staging_dir = Path(tmp)
                ZipImporter.extract_safely(content, staging_dir)
                # Everything export_project_zip can produce is UTF-8 text
                # except image assets — read_text() on those (e.g. a PNG's
                # magic bytes) raised a UnicodeDecodeError, so import/export
                # was never actually round-trippable for a project with
                # any Theme asset in it. rglob (not iterdir) since
                # extract_safely lets LEGAL_TERMS_FILE_NAME stay
                # nested one level — every other file is still flat, so
                # this is a no-op change for them.
                files = {
                    file.relative_to(staging_dir).as_posix(): (
                        file.read_bytes() if file.suffix.lower() in IMAGE_EXTENSIONS
                        else file.read_text(encoding="utf-8")
                    )
                    for file in staging_dir.rglob("*") if file.is_file()
                }
        else:
            files = {"index.yml": content.decode("utf-8")}
        # Pulled out before AutomatonBuilder sees `files`; a malformed
        # sessions.json fails the whole upload rather than partially
        # succeeding. Actually imported only once the project commits below.
        raw_sessions = files.pop(SESSIONS_EXPORT_FILENAME, None)
        assert not isinstance(raw_sessions, bytes)  # .json is never in IMAGE_EXTENSIONS, always read as text above
        sessions_to_import = self._parse_sessions_export(raw_sessions)
        raw_tests = files.pop(TESTS_EXPORT_FILENAME, None)
        assert not isinstance(raw_tests, bytes)
        tests_to_import = self._parse_tests_export(raw_tests)
        return files, sessions_to_import, tests_to_import

    def _build_from_upload(
        self, content: bytes, content_type: str | None, *, force_project_id: str | None = None,
    ) -> tuple[Automaton, dict[str, str | bytes], list[dict], list[dict]]:
        """Extracts + validates one upload. `force_project_id` is
        create_new_project's own escape hatch: the template's own declared
        id is already taken after the very first use, so that caller
        rewrites it to a freshly minted one before this builds/validates."""
        try:
            files, sessions_to_import, tests_to_import = self._extract_upload_files(content, content_type)
            index_yml = files.get("index.yml")
            if not isinstance(index_yml, str):
                raise ValueError("Upload must contain an 'index.yml'.")
            if force_project_id is not None:
                editor = AutomatonYamlEditor(index_yml)
                editor.set_project_field("id", force_project_id)
                files["index.yml"] = index_yml = editor.serialize()
            declared_id, declared_family, _ = AutomatonBuilder.read_declared_env_keys(index_yml)
            if declared_id is None:
                raise ValueError(
                    "project.id is required and must be a valid identifier "
                    "(letters, digits, underscores, not starting with a digit)."
                )
            automaton = AutomatonBuilder().build(
                files, self._automaton_loader.known_projects_env_keys(declared_id, declared_family)
            )
        except AutomatonBuildError:
            raise
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.exception(exc)
            raise ValueError(f"Invalid project definition: {exc}") from exc
        return automaton, files, sessions_to_import, tests_to_import

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        """Creates a project from a raw body (a zip archive, or a single
        bare YAML file), or — when its own project.id already names an
        existing project — adds a new revision on top of it instead. The
        uploaded project.id is always what's used; there is no separate
        "requested name" to fall back to or dedupe against anymore (see
        PROJECT_SPECS.md §2.1/§2.2).

        A re-upload of an existing id is accepted only if its own
        project.revision is absent (back-compat: auto-numbered as
        published + 1) or strictly greater than the currently published
        revision — otherwise rejected outright, nothing persisted. Either
        way the result is published immediately (see publish_project),
        same as a brand-new upload — an upload is meant to be usable right away.

        Extract -> validate -> persist -> publish -> commit happen here,
        synchronously (the persist/commit step needs the chat lock, which
        only ever runs safely on the caller's own event loop). The bundled
        sessions.json/tests.json, if any, are returned as an unprepared
        ProjectImportBundleJob instead of imported inline — one entry at
        a time, so a large re-import reports real progress instead of
        blocking. The caller decides what to do with it: submit it to
        the real job queue (SettingsController.put_project), or just
        drop it where nothing was ever bundled to import (create_new_project's
        built-in template)."""
        automaton, files, sessions_to_import, tests_to_import = self._build_from_upload(content, content_type)
        project_id = automaton.project_id
        existing_published = (
            self._db.get_project_published_revision(project_id) if self._db.project_exists(project_id) else None
        )
        declared_revision = AutomatonBuilder.peek_declared_revision(files["index.yml"])

        if existing_published is not None:
            if declared_revision is not None and declared_revision <= existing_published:
                raise ValueError(
                    f"Project '{project_id}': uploaded revision {declared_revision} is not newer than the "
                    f"currently published revision {existing_published} — import rejected."
                )
            final_revision = declared_revision if declared_revision is not None else existing_published + 1
        else:
            final_revision = declared_revision if declared_revision is not None else 0

        return await self._persist_uploaded_project(
            project_id, final_revision, automaton, files, sessions_to_import, tests_to_import, commit,
        )

    async def _persist_uploaded_project(
        self, project_id: str, revision: int, automaton: Automaton, files: dict[str, str | bytes],
        sessions_to_import: list[dict], tests_to_import: list[dict], commit: CommitCallback,
    ) -> tuple[dict, ProjectImportBundleJob]:
        # content_type is inferred from each entry's own extension, same
        # as put_project_file — image extensions (a zip's aspect/ assets
        # arrive as bytes, see _extract_upload_files) must resolve through
        # IMAGE_CONTENT_TYPE_BY_EXTENSION, not the text-only map: falling
        # through to TEXT_CONTENT_TYPE_BY_EXTENSION's own "text/plain"
        # default for e.g. aspect/icon.svg broke every image asset on a
        # zip export -> reimport round trip.
        files_bytes = {
            name: value.encode("utf-8") if isinstance(value, str) else value
            for name, value in files.items()
        }
        content_types = {
            name: (
                IMAGE_CONTENT_TYPE_BY_EXTENSION[Path(name).suffix.lower()]
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                else TEXT_CONTENT_TYPE_BY_EXTENSION.get(Path(name).suffix.lower(), "text/plain")
            )
            for name in files
        }
        is_new_project = not self._db.project_exists(project_id)
        if not is_new_project:
            self._db.reset_project(project_id)
        self._db.import_new_revision(project_id, revision, files_bytes, content_types)
        self._db.set_active_project_id(project_id, Session().user)
        await self.finalize_update(project_id, automaton, commit, is_new_project=is_new_project)
        self.publish_project(project_id)

        job = ProjectImportBundleJob(
            self._session_import_manager, self._db, project_id, sessions_to_import, tests_to_import
        )
        return {"success": True, "project_id": project_id}, job

    @staticmethod
    def _parse_sessions_export(raw_sessions: str | None) -> list[dict]:
        """None -> []. Otherwise must be a JSON array of session objects;
        anything else raises ValueError."""
        if raw_sessions is None:
            return []
        try:
            parsed = json.loads(raw_sessions)
        except json.JSONDecodeError as exc:
            raise ValueError(f"'{SESSIONS_EXPORT_FILENAME}' is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"'{SESSIONS_EXPORT_FILENAME}' must be a JSON array of sessions.")
        return parsed

    @staticmethod
    def _parse_tests_export(raw_tests: str | None) -> list[dict]:
        if raw_tests is None:
            return []
        try:
            parsed = json.loads(raw_tests)
        except json.JSONDecodeError as exc:
            raise ValueError(f"'{TESTS_EXPORT_FILENAME}' is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"'{TESTS_EXPORT_FILENAME}' must be a JSON array of test results.")
        return parsed

    def _unique_project_id(self, base: str) -> str:
        """`base` itself if free, else the first "`base`_N" (N starting
        at 2) not already in use."""
        existing = set(self._db.list_projects())
        if base not in existing:
            return base
        suffix = 2
        while f"{base}_{suffix}" in existing:
            suffix += 1
        return f"{base}_{suffix}"

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        """Creates a project from NEW_PROJECT_TEMPLATE — same validation/
        persist/publish path as a real upload (same (result, job)
        contract; the caller submits `job` to the real job queue, same as
        any other upload, though the built-in template never bundles any
        sessions/tests to import), except the template's own declared
        project.id is always rewritten to a freshly minted one first
        (_unique_project_id) — id must be globally unique, so the same
        template can't be reused verbatim on a second click."""
        content = NEW_PROJECT_TEMPLATE.read_bytes()
        template_files, _, _ = self._extract_upload_files(content, "application/zip")
        base_id, _, _ = AutomatonBuilder.read_declared_env_keys(template_files["index.yml"])
        project_id = self._unique_project_id(base_id or "hello_world")
        automaton, files, sessions_to_import, tests_to_import = self._build_from_upload(
            content, "application/zip", force_project_id=project_id,
        )
        return await self._persist_uploaded_project(
            project_id, automaton.project_revision, automaton, files, sessions_to_import, tests_to_import, commit,
        )

    def export_project_zip(self, project_id: str) -> bytes:
        """`project_id`'s files, round-trippable back through
        put_project, plus a SESSIONS_EXPORT_FILENAME holding every
        *imported* session, omitted when there are none. Everything under
        CACHE_DIR (a source's own per-session read cache — see
        tracking.sources.avance_archive) is omitted entirely: pure runtime
        scratch space, never part of a project's own versioned definition."""
        archives = self._db.get_archives(project_id)
        if archives is None:
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for archive_name, archive_content in archives.items():
                if archive_name.startswith(f"{CACHE_DIR}/"):
                    continue
                zf.writestr(archive_name, archive_content)
            exported_sessions = self._session_export_manager.export_sessions(
                None, project_id, type=('live', 'imported'),
            )
            for session in exported_sessions:
                session['type'] = 'imported'
            if exported_sessions:
                zf.writestr(SESSIONS_EXPORT_FILENAME, json.dumps(exported_sessions, indent=2))
            test_results = self._db.list_test_aggregate_results(project_id)
            if test_results:
                zf.writestr(TESTS_EXPORT_FILENAME, json.dumps(test_results, indent=2))

        return buffer.getvalue()

    async def delete_project(self, project_id: str, commit: CommitCallback) -> None:
        # Captured before delete_archives: that call drops the Project
        # row itself, and active_project_id is a real FK onto it with
        # on_delete='SET NULL' — by the time it returns, every user who
        # had this project active (including this one) already reads
        # back None, so checking afterwards could never see a match.
        was_active = project_id == self._inspector.get_active_project_id()
        self._db.reset_project(project_id)
        self._db.delete_archives(project_id)
        self._automaton_loader.invalidate_cache(project_id)
        # No AvailabilityChanged fires for a deletion (the project isn't
        # merely unavailable, it's gone), so its observers would otherwise
        # never notice their dependency vanished — recompute them directly,
        # same cascade a live id change triggers (see finalize_update).
        self._recheck_dependents_of_changed_id(project_id, project_id, None)

        if was_active:
            # Falls back to whatever's left, or nothing at all (the
            # "select a project" empty state) if that was the last one.
            remaining = self._db.list_projects()
            fallback = next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
            else:
                self._db.clear_active_project_id(Session().user)
