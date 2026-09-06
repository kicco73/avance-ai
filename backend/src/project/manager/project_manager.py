from __future__ import annotations

import io
import json
import zipfile
from typing import Mapping

from automaton.automaton import Automaton
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from chat.session_manager import ChatSessionManager
from db import Db
from logging_factory import LoggerFactory
from session import Session
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

from ..health import ProjectHealthChecker
from ..inspector import ProjectInspector
from ..archive.automaton_loader import AutomatonLoader
from ..archive.layout import (
    CACHE_DIR, ArchiveLayout, LEGAL_TERMS_FILE_NAME, SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME,
)
from ..project_import_bundle_job import ProjectImportBundleJob
from .availability import ProjectAvailability
from .uploader import ProjectUploader
from ..types import FAMILY_NOT_CHECKED, CommitCallback

logger = LoggerFactory.get_logger(__name__)

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
        self._availability = ProjectAvailability(db, self._health_checker, automaton_loader)
        self._uploader = ProjectUploader(db, automaton_loader, session_import_manager, self)

    def recompute_availability(self, project_id: str) -> None:
        self._availability.recompute(project_id)

    def recompute_all_availability(self) -> None:
        self._availability.recompute_all()

    def ensure_project_not_broken(self, project_id: str) -> None:
        self._availability.ensure_project_not_broken(project_id)

    def register_availability_cascade(self) -> None:
        self._availability.register_cascade()

    def get_runtime_status(self) -> list[dict]:
        return self._availability.get_runtime_status()

    def set_manually_paused(self, project_id: str) -> dict:
        return self._availability.set_manually_paused(project_id)

    def set_manually_running(self, project_id: str) -> dict:
        return self._availability.set_manually_running(project_id)

    def get_project_runtime_status(self, project_id: str) -> dict:
        return self._availability.get_project_runtime_status(project_id)

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        return self._availability.get_project_availability(project_id)

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        return await self._uploader.put_project(content, content_type, commit)

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        return await self._uploader.create_new_project(commit)

    def accept_legal_terms(self, username: str, project_id: str) -> None:
        current = self._db.get_archive_row(
            project_id, LEGAL_TERMS_FILE_NAME, revision=self._inspector.get_published_revision(project_id)
        )
        if current is None:
            return
        self._db.record_terms_acceptance(username, project_id, current.id)

    def _project_update_changed(self, existing: Mapping[str, str | bytes], files: Mapping[str, str | bytes]) -> bool:
        return any(name not in existing or existing[name] != content for name, content in files.items())

    def prepare_update(
        self, project_id: str, files: Mapping[str, str | bytes]
    ) -> tuple[Automaton, dict[str, str | bytes] | None]:
        existing = ArchiveLayout.decode_text(self._db.get_archives(project_id))
        merged = {**existing, **files}

        declared_id, declared_family, _ = AutomatonBuilder.read_declared_env_keys(merged["index.yml"])
        try:
            automaton = AutomatonBuilder().build(
                merged, self._automaton_loader.known_projects_env_keys(declared_id or project_id, declared_family)
            )
        except AutomatonBuildError as exc:
            exc.project_id = exc.project_id or project_id
            exc.revision = self._db.get_project_revision(project_id)
            raise
        self._validate_project_id_globally_unique(project_id, automaton.project_id)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _validate_project_id_globally_unique(self, project_id: str, new_project_id: str) -> None:
        if new_project_id == project_id:
            return
        if self._db.project_exists(new_project_id):
            raise ValueError(
                f"project.id '{new_project_id}' is already used by another project — "
                "project.id must be globally unique."
            )

    async def finalize_update(
        self, project_id: str, automaton: Automaton, commit: CommitCallback, *,
        is_new_project: bool = False, old_family: str | None | object = FAMILY_NOT_CHECKED,
    ) -> str:
        if automaton.project_id != project_id:
            old_project_id = project_id
            self._db.rename_project_id(old_project_id, automaton.project_id)
            self._automaton_loader.invalidate_cache(old_project_id)
            project_id = automaton.project_id
            self._availability.recheck_dependents_of_changed_id(project_id, old_project_id, project_id)
        elif is_new_project or (old_family is not FAMILY_NOT_CHECKED and old_family != automaton.family):
            self._availability.recheck_dependents_of_changed_id(project_id, project_id, project_id)

        self._db.set_project_metadata(project_id, automaton.project_ui_label, automaton.project_ui_description)

        revision = self._db.get_project_revision(project_id)
        automaton.set_storage_location(revision)
        self._automaton_loader.set_cached(project_id, revision, automaton)
        observed_project_ids = self._availability.filter_resolvable_project_ids(ProjectAvailability.automaton_project_refs(automaton))
        self._db.set_project_observers(project_id, observed_project_ids)
        self._availability.recompute(project_id)
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
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        self._db.revert_to_published(project_id)
        self._automaton_loader.invalidate_cache(project_id)
        new_automaton = self._automaton_loader.load(project_id)
        project_id = await self.finalize_update(project_id, new_automaton, commit)
        return self._inspector.get_project_revision_info(project_id)

    async def activate_project(self, project_id: str, commit: CommitCallback) -> Automaton:
        new_automaton = self._automaton_loader.load(project_id)
        self._db.ensure_project(project_id)
        self._db.set_active_project_id(project_id, Session().user)
        await commit(project_id, new_automaton)
        return new_automaton

    async def activate_project_idempotent(self, project_id: str, commit: CommitCallback) -> Automaton:
        new_automaton = self._automaton_loader.load(project_id)
        if project_id == self._inspector.get_active_project_id():
            return new_automaton
        return await self.activate_project(project_id, commit)

    def export_project_zip(self, project_id: str) -> bytes:
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
        was_active = project_id == self._inspector.get_active_project_id()
        self._db.reset_project(project_id)
        self._db.delete_archives(project_id)
        self._automaton_loader.invalidate_cache(project_id)
        self._availability.recheck_dependents_of_changed_id(project_id, project_id, None)

        if was_active:
            remaining = self._db.list_projects()
            fallback = next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
            else:
                self._db.clear_active_project_id(Session().user)
