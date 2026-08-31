from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping

from automaton.automaton import Automaton
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.automaton_builder import AutomatonBuilder
from db import Db
from events import AvailabilityChanged, publish, subscribe
from logging_factory import LoggerFactory
from session import Session
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

from .inspector import ProjectInspector
from .archive.automaton_loader import AutomatonLoader
from .archive.layout import (
    ArchiveLayout, IMAGE_EXTENSIONS, LEGAL_TERMS_FILE_NAME, SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME,
    TEXT_CONTENT_TYPE_BY_EXTENSION,
)
from .archive.zip_importer import ZipImporter
from .project_import_bundle_job import ProjectImportBundleJob
from .types import CommitCallback

logger = LoggerFactory.get_logger(__name__)

# "New project" starts from this sample zip, resolved off this module's own
# location, not the cwd.
NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "projects" / "Hello world.zip"
NEW_PROJECT_NAME = "Hello world"


class ProjectManager:
    def __init__(
        self, db: Db, automaton_loader: AutomatonLoader, inspector: ProjectInspector,
        session_export_manager: SessionExportManager, session_import_manager: SessionImportManager,
    ) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        self._inspector = inspector
        self._session_export_manager = session_export_manager
        self._session_import_manager = session_import_manager

    @staticmethod
    def _automaton_project_refs(automaton: Automaton) -> set[str]:
        """Every project_id `automaton`'s self-loop actions reference via
        automaton.* — raw tokens, not yet resolved to a project_name."""
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
        return {
            project_id for project_id in project_ids
            if self._db.get_project_name_by_project_id(project_id) is not None
        }

    def _dependency_unavailable(self, dep_id: str) -> tuple[bool, str]:
        """(is_unavailable, display_label) for one observed dependency,
        named by id. No Project row left at all (deleted after this
        project came to observe it) counts as unavailable too — the id
        itself stands in as the label since there's no project_name left
        to show."""
        dep_name = self._db.get_project_name_by_project_id(dep_id)
        if dep_name is None:
            return True, dep_id
        is_paused, _ = self._db.get_project_availability(dep_name) or (True, None)
        return is_paused, dep_name

    def recompute_availability(self, project_name: str) -> None:
        """Available exactly when the build succeeds and every automaton.*
        dependency is itself available. Writes only on change — this is
        what makes it safe to call from a cascade with no cycle detection."""
        if self._db.get_manually_paused(project_name):
            available, reason = False, "Manually paused."
        else:
            try:
                self._automaton_loader.load(project_name)
                available, reason = True, None
            except Exception as exc:  # noqa: BLE001 — any failure to build at all means "not available"
                available, reason = False, f"Build failed: {exc}"

            if available:
                blocking = None
                for dep_id in self._db.get_observed_projects(project_name):
                    is_unavailable, label = self._dependency_unavailable(dep_id)
                    if is_unavailable:
                        blocking = label
                        break
                if blocking is not None:
                    available, reason = False, f"Depends on unavailable project '{blocking}'."

        current = self._db.get_project_availability(project_name)
        if current is None:
            return  # project no longer exists — nothing left to update
        was_paused, _ = current
        if was_paused == (not available):
            return  # unchanged — see this method's own docstring on why this is the whole guard
        self._db.set_project_availability(project_name, is_paused=not available, paused_reason=reason)
        publish(AvailabilityChanged(project_name=project_name, available=available))

    def _observers_of(self, project_name: str) -> list[str]:
        """Every project observing `project_name` via automaton.* — the
        observer index is keyed by the observed project's id, so this
        translates name -> id first. A project with no declared id can't
        be referenced by anyone, so it trivially has no observers."""
        project_id = self._db.get_project_id(project_name)
        return self._db.get_observers(project_id) if project_id else []

    def _recheck_dependents_of_changed_id(
        self, project_name: str, old_project_id: str | None, new_project_id: str | None,
    ) -> None:
        """`project_name` just stopped answering to one id and/or started
        answering to another (a brand new project's id counts as
        "started", coming from None).

        A project observing the *old* id is already correctly indexed —
        that reference just became unresolvable, so a plain recompute is
        enough (same as a delete). A project merely waiting on the *new*
        one was never indexed at all if the id didn't resolve to anything
        until now (see _filter_resolvable_project_ids silently dropping
        it) — those can only be found by re-scanning every project's raw
        triggers directly, and need their own observer-index row
        refreshed before a recompute of theirs means anything."""
        affected: set[str] = set()
        if old_project_id is not None:
            affected |= set(self._db.get_observers(old_project_id))

        if new_project_id is not None:
            for other_name in self._db.list_projects():
                if other_name == project_name:
                    continue
                try:
                    other_automaton = self._automaton_loader.load(other_name)
                except Exception:  # noqa: BLE001 — a broken build has nothing to refresh
                    continue
                other_refs = self._automaton_project_refs(other_automaton)
                if new_project_id not in other_refs:
                    continue
                self._db.set_project_observers(other_name, self._filter_resolvable_project_ids(other_refs))
                affected.add(other_name)

        affected.discard(project_name)
        for observer in affected:
            self.recompute_availability(observer)

    def register_availability_cascade(self) -> None:
        """Subscribed once, for the process's lifetime. Recursive by
        construction: recompute_availability's write-only-on-change guard
        is what makes the cascade stop propagating on its own."""
        subscribe(AvailabilityChanged, self._on_availability_changed)

    def _on_availability_changed(self, event: AvailabilityChanged) -> None:
        try:
            for observer in self._observers_of(event.project_name):
                self.recompute_availability(observer)
        except Exception:
            logger.exception(
                "Availability cascade failed while reacting to '%s' (available=%s).",
                event.project_name, event.available,
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
        """One row per project for the Settings > Runtime status view."""
        return [
            {
                "name": row["name"],
                "status": self._project_status(row["is_paused"], row["manually_paused"]),
                "paused_reason": row["paused_reason"],
                "revision": row["revision"],
                "published_revision": row["published_revision"],
            }
            for row in self._db.list_projects_runtime_status()
        ]

    def accept_legal_terms(self, username: str, project_name: str) -> None:
        """Records that `username` has accepted `project_name`'s current
        legal/terms.md — a no-op if the project has none. Always accepts
        whichever *published* Archive row is current right now, matching
        legal_terms_pending's own revision — never the draft's, which can
        hold a different row id for the same file the moment the project
        has any unpublished edit: if the published terms changed again in
        between, the next legal_terms_pending check will correctly ask again."""
        current = self._db.get_archive_row(
            project_name, LEGAL_TERMS_FILE_NAME, revision=self._inspector.get_published_revision(project_name)
        )
        if current is None:
            return
        self._db.record_terms_acceptance(username, project_name, current.id)

    def set_manually_paused(self, project_name: str) -> dict:
        """Only allowed from 'running', enforced here rather than left to
        the UI alone. Recomputing afterward reuses the normal
        AvailabilityChanged cascade rather than a separate one."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        status = self._project_status(is_paused, manually_paused)
        if status != "running":
            raise ValueError(f"Project '{project_name}' isn't running (status: '{status}') — can't be manually paused.")
        self._db.set_manually_paused(project_name, True)
        self.recompute_availability(project_name)
        return self.get_project_runtime_status(project_name)

    def set_manually_running(self, project_name: str) -> dict:
        """Only allowed from 'manually_paused'. Clears the flag and lets
        recompute_availability report the real state again, which may
        still be unavailable if a dependency went down in the meantime."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        status = self._project_status(is_paused, manually_paused)
        if status != "manually_paused":
            raise ValueError(f"Project '{project_name}' isn't manually paused (status: '{status}') — can't be resumed.")
        self._db.set_manually_paused(project_name, False)
        self.recompute_availability(project_name)
        return self.get_project_runtime_status(project_name)

    def get_project_runtime_status(self, project_name: str) -> dict:
        """One row, same shape as get_runtime_status — lets the
        pause/resume endpoints refresh just this row."""
        is_paused, paused_reason = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        return {
            "name": project_name,
            "status": self._project_status(is_paused, manually_paused),
            "paused_reason": paused_reason,
            "revision": self._db.get_project_revision(project_name),
            "published_revision": self._db.get_project_published_revision(project_name),
        }

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None]:
        """(is_paused, paused_reason). Returns (False, None), never
        raises, for a project that doesn't exist at all."""
        return self._db.get_project_availability(project_name) or (False, None)

    def _project_update_changed(self, existing: Mapping[str, str | bytes], files: Mapping[str, str | bytes]) -> bool:
        """Whether `files` is a genuine change against `existing`. A file
        `existing` doesn't have yet always counts as changed, even with ""
        content — a brand-new empty file must still get persisted."""
        return any(name not in existing or existing[name] != content for name, content in files.items())

    def prepare_update(
        self, project_name: str, files: Mapping[str, str | bytes]
    ) -> tuple[Automaton, dict[str, str | bytes] | None]:
        """Builds+validates the Automaton for `files` merged onto
        `project_name`'s current files. Read-only. Returns (automaton,
        to_persist), where to_persist is None if nothing actually changed."""
        existing = ArchiveLayout.decode_text(self._db.get_archives(project_name))
        merged = {**existing, **files}

        automaton = AutomatonBuilder().build(merged, self._automaton_loader.known_projects_env_keys(project_name))
        self._validate_project_id_globally_unique(project_name, automaton.project_id)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _validate_project_id_globally_unique(self, project_name: str, project_id: str | None) -> None:
        """The one project.id check AutomatonBuilder can't do itself:
        whether some *other* project has already claimed it. Raises before
        anything gets persisted, so a failed save leaves nothing partial."""
        if project_id is None:
            return
        owner = self._db.get_project_name_by_project_id(project_id)
        if owner is not None and owner != project_name:
            raise ValueError(
                f"project.id '{project_id}' is already used by project '{owner}' — "
                "project.id must be globally unique."
            )

    async def finalize_update(
        self, project_name: str, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Called by every project-mutating path before awaiting `commit`.
        Refreshes the automaton cache and resets the active project's live
        conversation only when its current state no longer exists."""

        # Captured before set_project_metadata overwrites it, so a changed
        # (or brand new, previously None) id can be told apart from an
        # unchanged one below.
        old_project_id = self._db.get_project_id(project_name)

        # Synced first: this project's own project_id must be current
        # before any *other* project's build can resolve a reference to it.
        self._db.set_project_metadata(
            project_name, automaton.project_id, automaton.project_ui_label, automaton.project_ui_description,
        )

        revision = self._db.get_project_revision(project_name)
        self._automaton_loader.set_cached(project_name, revision, automaton)
        # Reverse index of every project this one's self-loop actions
        # reference via automaton.* — recomputed on every successful build
        # regardless of whether `project_name` is currently active.
        observed_project_ids = self._filter_resolvable_project_ids(self._automaton_project_refs(automaton))
        self._db.set_project_observers(project_name, observed_project_ids)
        self.recompute_availability(project_name)
        if automaton.project_id != old_project_id:
            self._recheck_dependents_of_changed_id(project_name, old_project_id, automaton.project_id)
        if project_name == self._inspector.get_active_project_name():
            current_state_key = self._db.get_current_state(project_name)
            if current_state_key is None or current_state_key not in automaton.states:
                self._db.reset_project(project_name)
            await commit(project_name, automaton)
            return True
        return False

    def reset_test_sessions(self, project_name: str) -> None:
        self._db.reset_project_for_user(Session().user, project_name, type='test')

    def wipe_live_sessions(self, project_name: str) -> None:
        self._db.wipe_live_sessions_for_project(project_name)

    def preview_publish(self, project_name: str) -> dict:
        """Whether publishing `project_name` needs a human state remap
        decision: the current persisted state has gone missing from the
        draft about to be published. Also reports `has_active_sessions`."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        draft = self._automaton_loader.load(project_name)
        current_state_key = self._db.get_current_state(project_name, type='live')
        published_revision = self._db.get_project_published_revision(project_name)
        has_active_sessions = (
            published_revision is not None
            and self._db.has_open_sessions_for_revision(project_name, published_revision)
        )
        if current_state_key is None or current_state_key in draft.states:
            return {"needs_remap": False, "has_active_sessions": has_active_sessions}
        return {
            "needs_remap": True,
            "missing_state": current_state_key,
            "available_states": [state.key for state in draft.states.values() if state.key != ""],
            "has_active_sessions": has_active_sessions,
        }

    def publish_project(self, project_name: str, remap_to: str | None = None) -> dict:
        """Sets published_revision = revision, freezing the current draft.
        If the persisted state has gone missing from it, `remap_to` must
        name a real state; the StateRemap written is consulted from then on."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        draft = self._automaton_loader.load(project_name)
        current_state_key = self._db.get_current_state(project_name, type='live')
        if current_state_key is not None and current_state_key not in draft.states:
            if remap_to is None:
                raise ValueError(
                    f"State '{current_state_key}' no longer exists in this revision — a remap target is required."
                )
            if remap_to == "" or remap_to not in draft.states:
                raise ValueError(f"'{remap_to}' is not a valid state in this revision.")
            self._db.write_state_remap(project_name, current_state_key, remap_to)
        self._db.publish_project(project_name)
        return self._inspector.get_project_revision_info(project_name)

    async def revert_to_published(self, project_name: str, commit: CommitCallback) -> dict:
        """Discards the entire in-progress draft revision, reverting to
        whatever was last published."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._db.revert_to_published(project_name)
        self._automaton_loader.invalidate_cache(project_name)
        new_automaton = self._automaton_loader.load(project_name)
        await self.finalize_update(project_name, new_automaton, commit)
        return self._inspector.get_project_revision_info(project_name)

    async def activate_project(self, project_name: str, commit: CommitCallback) -> Automaton:
        """Validates via _load_and_validate(), persists `project_name` as
        active, then awaits `commit(project_name, new_automaton)`."""
        new_automaton = self._automaton_loader.load(project_name)
        # active_project_id is a real FK onto Project.name — for a brand
        # new project (commit below is what first calls save_project_files/
        # ensure_project), the row doesn't exist yet at this point, so it
        # has to be ensured here first. Idempotent, so a no-op for an
        # already-persisted project.
        self._db.ensure_project(project_name)
        self._db.set_active_project_name(project_name, Session().user)
        await commit(project_name, new_automaton)
        return new_automaton

    async def activate_project_idempotent(self, project_name: str, commit: CommitCallback) -> Automaton:
        """Always validates `project_name` first, even if already active —
        idempotency only skips the swap + commit, never the correctness
        checks. A different project delegates to activate_project()."""
        new_automaton = self._automaton_loader.load(project_name)
        if project_name == self._inspector.get_active_project_name():
            return new_automaton
        return await self.activate_project(project_name, commit)

    async def put_project(
        self, project_name: str, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        """Creates or replaces `project_name` from a raw body — a zip
        archive, or a single bare YAML file treated as index.yml's own
        content with no attachments. Extract -> validate -> persist ->
        commit happen here, synchronously (the last one needs the chat
        lock, which only ever runs safely on the caller's own event loop).
        The bundled sessions.json/tests.json, if any, are returned as
        an unprepared ProjectImportBundleJob instead of imported inline —
        one entry at a time, so a large re-import reports real progress
        instead of blocking. The caller decides what to do with it: submit
        it to the real job queue (SettingsController.put_project), or just
        drop it where nothing was ever bundled to import (create_new_project's
        built-in template)."""

        if not self._automaton_loader.is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")
        if project_name in self._db.list_projects():
            raise ValueError(f"A project named '{project_name}' already exists.")

        try:
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
            new_automaton, to_persist = self.prepare_update(project_name, files)
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.exception(exc)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        # ensure_project first: active_project_id is a real FK onto
        # Project.name, and this project's own row doesn't exist yet for
        # a brand new project.
        self._db.ensure_project(project_name)
        self._db.set_active_project_name(project_name, Session().user)
        if to_persist is not None:
            # This upload path is text-only; content_type is inferred from
            # each entry's own extension, same as put_project_file.
            to_persist_bytes = {
                name: value.encode("utf-8") if isinstance(value, str) else value
                for name, value in to_persist.items()
            }
            content_types = {
                name: TEXT_CONTENT_TYPE_BY_EXTENSION.get(Path(name).suffix.lower(), "text/plain")
                for name in to_persist
            }
            self._db.save_project_files(project_name, to_persist_bytes, content_types)
        await self.finalize_update(project_name, new_automaton, commit)

        job = ProjectImportBundleJob(
            self._session_import_manager, self._db, project_name, sessions_to_import, tests_to_import
        )
        return {"success": True, "project_name": project_name}, job

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

    def _unique_project_name(self, base: str) -> str:
        """`base` itself if free, else the first "`base` N" (N starting
        at 2) not already in use."""
        existing = set(self._db.list_projects())
        if base not in existing:
            return base
        suffix = 2
        while f"{base} {suffix}" in existing:
            suffix += 1
        return f"{base} {suffix}"

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        """Creates a project from NEW_PROJECT_TEMPLATE, going through
        put_project so validation/staging/commit stay identical to a real
        upload — same (result, job) contract; the caller submits `job` to
        the real job queue, same as any other upload."""
        content = NEW_PROJECT_TEMPLATE.read_bytes()
        project_name = self._unique_project_name(NEW_PROJECT_NAME)
        return await self.put_project(project_name, content, "application/zip", commit)

    def export_project_zip(self, project_name: str) -> bytes:
        """`project_name`'s files, round-trippable back through
        put_project, plus a SESSIONS_EXPORT_FILENAME holding every
        *imported* session, omitted when there are none."""
        archives = self._db.get_archives(project_name)
        if archives is None:
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for archive_name, archive_content in archives.items():
                zf.writestr(archive_name, archive_content)
            exported_sessions = self._session_export_manager.export_sessions(
                None, project_name, type=('live', 'imported'),
            )
            for session in exported_sessions:
                session['type'] = 'imported'
            if exported_sessions:
                zf.writestr(SESSIONS_EXPORT_FILENAME, json.dumps(exported_sessions, indent=2))
            test_results = self._db.list_test_aggregate_results(project_name)
            if test_results:
                zf.writestr(TESTS_EXPORT_FILENAME, json.dumps(test_results, indent=2))

        return buffer.getvalue()

    async def delete_project(self, project_name: str, commit: CommitCallback) -> None:
        # Captured before delete_archives: that call drops the Project
        # row itself, and active_project_id is a real FK onto it with
        # on_delete='SET NULL' — by the time it returns, every user who
        # had this project active (including this one) already reads
        # back None, so checking afterwards could never see a match.
        was_active = project_name == self._inspector.get_active_project_name()
        self._db.reset_project(project_name)
        old_project_id = self._db.get_project_id(project_name)
        self._db.delete_archives(project_name)
        self._automaton_loader.invalidate_cache(project_name)
        # No AvailabilityChanged fires for a deletion (the project isn't
        # merely unavailable, it's gone), so its observers would otherwise
        # never notice their dependency vanished — recompute them directly,
        # same cascade a live id change triggers (see finalize_update).
        self._recheck_dependents_of_changed_id(project_name, old_project_id, None)

        if was_active:
            # Falls back to whatever's left, or nothing at all (the
            # "select a project" empty state) if that was the last one.
            remaining = self._db.list_projects()
            fallback = next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
            else:
                self._db.clear_active_project_name(Session().user)
