"""Composition root for the project subsystem — wires AutomatonLoader
(project/archive/automaton_loader.py), ProjectInspector (project/inspector.py),
ProjectManager (project/manager.py), and ProjectEditor (project/editor.py),
then exposes every one of their public methods under a single facade so no
external caller (chat/, tracking/, controllers/) needs to know which
collaborator actually does the work."""
from __future__ import annotations

from typing import TYPE_CHECKING

from automaton.automaton import (
    Action, ActionPayload, Automaton, EnvKeyPayload, ProjectPayload, SignalPayload, State, StatePayload,
)
from db import Db
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

from .editor import ProjectEditor
from .inspector import ProjectInspector
from .manager import ProjectManager
from .archive.automaton_loader import AutomatonLoader
from .project_import_bundle_job import ProjectImportBundleJob
from .types import CommitCallback

if TYPE_CHECKING:
    from ai.ai_service import AiService

__all__ = ["ProjectService", "CommitCallback"]


class ProjectService(object):
    def __init__(self, db: Db, ai_service: "AiService | None" = None) -> None:
        self._db = db
        session_export_manager = SessionExportManager(db)
        session_import_manager = SessionImportManager(db)
        self._automaton_loader = AutomatonLoader(db)
        self._inspector = ProjectInspector(db, self._automaton_loader, ai_service)
        self._manager = ProjectManager(
            db, self._automaton_loader, self._inspector, session_export_manager, session_import_manager
        )
        self._editor = ProjectEditor(db, self._automaton_loader, self._inspector, self._manager)

    # -- ProjectInspector -------------------------------------------------

    def get_active_project_name(self) -> str:
        return self._inspector.get_active_project_name()

    def get_published_revision(self, project_name: str) -> int:
        return self._inspector.get_published_revision(project_name)

    def get_draft_revision(self, project_name: str) -> int:
        return self._inspector.get_draft_revision(project_name)

    def legal_terms_pending(self, username: str, project_name: str) -> bool:
        return self._inspector.legal_terms_pending(username, project_name, revision=self.get_published_revision(project_name))

    def get_legal_terms_status(self, username: str, project_name: str) -> dict:
        return self._inspector.get_legal_terms_status(username, project_name, revision=self.get_published_revision(project_name))

    def get_automaton(self, project_name: str, revision: int) -> Automaton:
        return self._inspector.get_automaton(project_name, revision)

    def get_active_automaton(self) -> Automaton:
        return self._inspector.get_active_automaton()

    def get_automaton_and_state(
        self, project_name: str, type: str = 'live', username: str | None = None
    ) -> tuple[Automaton, State]:
        return self._inspector.get_automaton_and_state(project_name, type, username)

    def get_active_automaton_and_state(self, username: str | None = None) -> tuple[Automaton, State]:
        return self._inspector.get_active_automaton_and_state(username)

    def get_automaton_for_session(self, session_id: int) -> Automaton:
        return self._inspector.get_automaton_for_session(session_id)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        return self._inspector.get_automaton_and_state_for_session(session_id)

    def get_automaton_and_state_for_observer(
        self, project_name: str, username: str
    ) -> tuple[Automaton, State] | None:
        return self._inspector.get_automaton_and_state_for_observer(project_name, username)

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        return self._inspector.apply_manual_action(action_name, session_id)

    def get_active_state_payload(self) -> StatePayload:
        return self._inspector.get_active_state_payload()

    def get_project_signals(
        self, project_name: str, state_key: str | None = None, session_id: int | None = None
    ) -> list[dict]:
        return self._inspector.get_project_signals(project_name, state_key, session_id)

    def get_project_env_keys(self, project_name: str, session_id: int | None = None) -> list[dict]:
        return self._inspector.get_project_env_keys(project_name, session_id)

    def get_project_metadata(self, project_name: str) -> ProjectPayload:
        return self._inspector.get_project_metadata(project_name)

    def get_identifier_registry(self, project_name: str) -> dict[str, dict[str, str]]:
        return self._inspector.get_identifier_registry(project_name)

    def get_project_states(self, project_name: str) -> list[str]:
        return self._inspector.get_project_states(project_name)

    def get_project_graph(self, project_name: str, session_id: int | None = None) -> dict:
        return self._inspector.get_project_graph(project_name, session_id)

    def get_state_input_tokens(self, project_name: str, state_key: str, session_id: int | None = None) -> int | None:
        return self._inspector.get_state_input_tokens(project_name, state_key, session_id)

    def list_projects(self) -> dict:
        return self._inspector.list_projects()

    def get_project_revision_info(self, project_name: str) -> dict:
        return self._inspector.get_project_revision_info(project_name)

    # -- ProjectManager -----------------------------------------------------

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None]:
        return self._manager.get_project_availability(project_name)

    def recompute_availability(self, project_name: str) -> None:
        self._manager.recompute_availability(project_name)

    def register_availability_cascade(self) -> None:
        self._manager.register_availability_cascade()

    def get_runtime_status(self) -> list[dict]:
        return self._manager.get_runtime_status()

    def accept_legal_terms(self, username: str, project_name: str) -> None:
        self._manager.accept_legal_terms(username, project_name)

    def set_manually_paused(self, project_name: str) -> dict:
        return self._manager.set_manually_paused(project_name)

    def set_manually_running(self, project_name: str) -> dict:
        return self._manager.set_manually_running(project_name)

    def get_project_runtime_status(self, project_name: str) -> dict:
        return self._manager.get_project_runtime_status(project_name)

    def reset_test_sessions(self, project_name: str) -> None:
        self._manager.reset_test_sessions(project_name)

    def wipe_live_sessions(self, project_name: str) -> None:
        self._manager.wipe_live_sessions(project_name)

    def preview_publish(self, project_name: str) -> dict:
        return self._manager.preview_publish(project_name)

    def publish_project(self, project_name: str, remap_to: str | None = None) -> dict:
        return self._manager.publish_project(project_name, remap_to)

    async def revert_to_published(self, project_name: str, commit: CommitCallback) -> dict:
        return await self._manager.revert_to_published(project_name, commit)

    async def activate_project(self, project_name: str, commit: CommitCallback) -> Automaton:
        return await self._manager.activate_project(project_name, commit)

    async def activate_project_idempotent(self, project_name: str, commit: CommitCallback) -> Automaton:
        return await self._manager.activate_project_idempotent(project_name, commit)

    async def put_project(
        self, project_name: str, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        return await self._manager.put_project(project_name, content, content_type, commit)

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        return await self._manager.create_new_project(commit)

    def export_project_zip(self, project_name: str) -> bytes:
        return self._manager.export_project_zip(project_name)

    async def delete_project(self, project_name: str, commit: CommitCallback) -> None:
        await self._manager.delete_project(project_name, commit)

    # -- ProjectEditor --------------------------------------------------

    def list_project_files(self, project_name: str) -> list[str]:
        return self._editor.list_project_files(project_name)

    def get_project_file(self, project_name: str, file_name: str) -> dict:
        return self._editor.get_project_file(project_name, file_name)

    def get_project_file_content(
        self, project_name: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        return self._editor.get_project_file_content(project_name, file_name, session_id)

    async def put_project_file(
        self, project_name: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        return await self._editor.put_project_file(project_name, file_name, content, content_type_header, commit)

    async def add_legal_terms(self, project_name: str, commit: CommitCallback) -> dict:
        return await self._editor.add_legal_terms(project_name, commit)

    async def add_state(self, project_name: str, commit: CommitCallback) -> StatePayload:
        return await self._editor.add_state(project_name, commit)

    async def add_signal(self, project_name: str, commit: CommitCallback) -> SignalPayload:
        return await self._editor.add_signal(project_name, commit)

    async def add_action(self, project_name: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self._editor.add_action(project_name, state_name, commit)

    async def set_state_field(
        self, project_name: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self._editor.set_state_field(project_name, state_name, field, value, commit)

    async def set_action_field(
        self, project_name: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self._editor.set_action_field(project_name, state_name, action_name, field, value, commit)

    async def set_signal_field(
        self, project_name: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self._editor.set_signal_field(project_name, signal_name, field, value, commit)

    async def set_init_action_field(self, project_name: str, field: str, value, commit: CommitCallback):
        return await self._editor.set_init_action_field(project_name, field, value, commit)

    async def set_project_field(self, project_name: str, field: str, value, commit: CommitCallback) -> ProjectPayload:
        return await self._editor.set_project_field(project_name, field, value, commit)

    async def delete_state(self, project_name: str, state_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_state(project_name, state_name, commit)

    async def delete_action(self, project_name: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_action(project_name, state_name, action_name, commit)

    async def delete_signal(self, project_name: str, signal_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_signal(project_name, signal_name, commit)

    async def add_env_key(self, project_name: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self._editor.add_env_key(project_name, commit)

    async def set_env_key_field(
        self, project_name: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self._editor.set_env_key_field(project_name, env_key_name, field, value, commit)

    async def delete_env_key(self, project_name: str, env_key_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_env_key(project_name, env_key_name, commit)

    async def reorder_actions(
        self, project_name: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self._editor.reorder_actions(project_name, state_name, action_name, position, commit)

    async def undo_project_file(self, project_name: str, file_name: str, content: bytes) -> dict:
        return await self._editor.undo_project_file(project_name, file_name, content)

    async def redo_project_file(self, project_name: str, file_name: str, content: bytes) -> dict:
        return await self._editor.redo_project_file(project_name, file_name, content)

    def clear_project_history(self, project_name: str) -> None:
        self._editor.clear_project_history(project_name)

    async def delete_project_file(self, project_name: str, file_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_project_file(project_name, file_name, commit)
