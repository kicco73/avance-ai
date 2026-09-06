"""Composition root for the project subsystem — wires AutomatonLoader
(project/archive/automaton_loader.py), ProjectInspector (project/inspector.py),
ProjectManager (project/manager/), and ProjectEditor (project/editor.py),
then exposes every one of their public methods under a single facade so no
external caller (chat/, tracking/, controllers/) needs to know which
collaborator actually does the work."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from automaton.automaton import (
    Action, ActionPayload, Automaton, EnvKeyPayload, ProjectPayload, SignalPayload, SourcePayload, State, StatePayload,
)
from chat.session_manager import ChatSessionManager
from db import Db
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

from .editor import ProjectEditor
from .inspector import ProjectInspector
from .invites import InviteManager
from .manager import ProjectManager
from .archive.automaton_loader import AutomatonLoader
from .project_import_bundle_job import ProjectImportBundleJob
from .types import CommitCallback

if TYPE_CHECKING:
    from ai import AiService

__all__ = ["ProjectService", "CommitCallback"]

_ICON_FILE_RE = re.compile(r'^aspect/icon\.(png|jpe?g|gif|webp|svg)$', re.IGNORECASE)


class ProjectService(object):
    def __init__(
        self, db: Db, ai_service: "AiService | None" = None,
        invite_valid_days: int = 7, invite_max_shares: int = 3, whatsapp_number: str | None = None,
        whatsapp_invite_prefix: str = "Invitation code: ",
        session_manager: ChatSessionManager | None = None,
    ) -> None:
        self._db = db
        if session_manager is None:
            session_manager = ChatSessionManager(db)
        session_export_manager = SessionExportManager(db)
        session_import_manager = SessionImportManager(db)
        self._automaton_loader = AutomatonLoader(db, session_manager=session_manager)
        self._inspector = ProjectInspector(db, self._automaton_loader, ai_service)
        self._manager = ProjectManager(
            db, self._automaton_loader, self._inspector, session_export_manager, session_import_manager,
            session_manager,
        )
        self._editor = ProjectEditor(db, self._automaton_loader, self._inspector, self._manager, ai_service)
        self._invites = InviteManager(db, invite_valid_days, invite_max_shares, whatsapp_number, whatsapp_invite_prefix)

    # -- ProjectInspector -------------------------------------------------

    def get_active_project_id(self) -> str:
        return self._inspector.get_active_project_id()

    def get_published_revision(self, project_id: str) -> int:
        return self._inspector.get_published_revision(project_id)

    def get_draft_revision(self, project_id: str) -> int:
        return self._inspector.get_draft_revision(project_id)

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return self._inspector.legal_terms_pending(username, project_id, revision=self.get_published_revision(project_id))

    def get_legal_terms_status(self, username: str, project_id: str) -> dict:
        return self._inspector.get_legal_terms_status(username, project_id, revision=self.get_published_revision(project_id))

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self._inspector.get_automaton(project_id, revision)

    def get_active_automaton(self) -> Automaton:
        return self._inspector.get_active_automaton()

    def get_automaton_and_state(
        self, project_id: str, type: str = 'live', username: str | None = None
    ) -> tuple[Automaton, State]:
        return self._inspector.get_automaton_and_state(project_id, type, username)

    def get_active_automaton_and_state(self, username: str | None = None) -> tuple[Automaton, State]:
        return self._inspector.get_active_automaton_and_state(username)

    def get_automaton_for_session(self, session_id: int) -> Automaton:
        return self._inspector.get_automaton_for_session(session_id)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        return self._inspector.get_automaton_and_state_for_session(session_id)

    def get_automaton_and_state_for_observer(
        self, project_id: str, username: str
    ) -> tuple[Automaton, State] | None:
        return self._inspector.get_automaton_and_state_for_observer(project_id, username)

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        return self._inspector.apply_manual_action(action_name, session_id)

    def get_active_state_payload(self) -> StatePayload:
        return self._inspector.get_active_state_payload()

    def get_project_signals(
        self, project_id: str, state_key: str | None = None, session_id: int | None = None
    ) -> list[dict]:
        return self._inspector.get_project_signals(project_id, state_key, session_id)

    def get_project_env_keys(self, project_id: str, session_id: int | None = None) -> list[dict]:
        return self._inspector.get_project_env_keys(project_id, session_id)

    def get_project_sources(self, project_id: str, session_id: int | None = None) -> list[dict]:
        return self._inspector.get_project_sources(project_id, session_id)

    def get_project_metadata(self, project_id: str) -> ProjectPayload:
        return self._inspector.get_project_metadata(project_id)

    def get_identifier_registry(self, project_id: str) -> dict[str, dict[str, str]]:
        return self._inspector.get_identifier_registry(project_id)

    def get_project_states(self, project_id: str) -> list[str]:
        return self._inspector.get_project_states(project_id)

    def get_project_graph(self, project_id: str, session_id: int | None = None) -> dict:
        return self._inspector.get_project_graph(project_id, session_id)

    def get_state_input_tokens(self, project_id: str, state_key: str, session_id: int | None = None) -> int | None:
        return self._inspector.get_state_input_tokens(project_id, state_key, session_id)

    def list_projects(self, username: str | None = None) -> dict:
        return self._inspector.list_projects(username)

    def get_project_revision_info(self, project_id: str) -> dict:
        return self._inspector.get_project_revision_info(project_id)

    # -- ProjectManager -----------------------------------------------------

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        return self._manager.get_project_availability(project_id)

    def recompute_availability(self, project_id: str) -> None:
        self._manager.recompute_availability(project_id)

    def recompute_all_availability(self) -> None:
        self._manager.recompute_all_availability()

    def ensure_project_not_broken(self, project_id: str) -> None:
        self._manager.ensure_project_not_broken(project_id)

    def register_availability_cascade(self) -> None:
        self._manager.register_availability_cascade()

    def get_runtime_status(self) -> list[dict]:
        return self._manager.get_runtime_status()

    def accept_legal_terms(self, username: str, project_id: str) -> None:
        self._manager.accept_legal_terms(username, project_id)

    # -- InviteManager ----------------------------------------------------

    def create_invite(self, project_id: str, created_by: str | None) -> dict:
        return self._invites.create_invite(project_id, created_by)

    def resolve_invite_link(self, code: str | None, user_id: str, role: str) -> str | None:
        return self._invites.resolve_invite_link(code, user_id, role)

    def validate_invite_for_registration(self, code: str | None):
        return self._invites.validate_for_registration(code)

    def redeem_invite(self, invite, user_id: str) -> None:
        self._invites.redeem(invite, user_id)

    def set_manually_paused(self, project_id: str) -> dict:
        return self._manager.set_manually_paused(project_id)

    def set_manually_running(self, project_id: str) -> dict:
        return self._manager.set_manually_running(project_id)

    def get_project_runtime_status(self, project_id: str) -> dict:
        return self._manager.get_project_runtime_status(project_id)

    def reset_test_sessions(self, project_id: str) -> None:
        self._manager.reset_test_sessions(project_id)

    def wipe_all_live_sessions(self) -> None:
        self._manager.wipe_all_live_sessions()

    def clean_unused_revisions(self) -> int:
        return self._manager.clean_unused_revisions()

    def preview_publish(self, project_id: str) -> dict:
        return self._manager.preview_publish(project_id)

    def publish_project(self, project_id: str, remap_to: str | None = None) -> dict:
        return self._manager.publish_project(project_id, remap_to)

    async def revert_to_published(self, project_id: str, commit: CommitCallback) -> dict:
        return await self._manager.revert_to_published(project_id, commit)

    async def activate_project(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self._manager.activate_project(project_id, commit)

    async def activate_project_idempotent(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self._manager.activate_project_idempotent(project_id, commit)

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        return await self._manager.put_project(content, content_type, commit)

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        return await self._manager.create_new_project(commit)

    def export_project_zip(self, project_id: str) -> bytes:
        return self._manager.export_project_zip(project_id)

    async def delete_project(self, project_id: str, commit: CommitCallback) -> None:
        await self._manager.delete_project(project_id, commit)

    # -- ProjectEditor --------------------------------------------------

    def list_project_files(self, project_id: str) -> list[str]:
        return self._editor.list_project_files(project_id)

    def get_project_file(self, project_id: str, file_name: str) -> dict:
        return self._editor.get_project_file(project_id, file_name)

    async def generate_index_yml_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self._editor.generate_index_yml_ai_edit(project_id, instruction)

    async def generate_index_css_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self._editor.generate_index_css_ai_edit(project_id, instruction)

    def get_project_file_content(
        self, project_id: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        return self._editor.get_project_file_content(project_id, file_name, session_id)

    async def put_project_file(
        self, project_id: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        return await self._editor.put_project_file(project_id, file_name, content, content_type_header, commit)

    async def rename_project_file(self, project_id: str, old_name: str, new_name: str, commit: CommitCallback) -> dict:
        return await self._editor.rename_project_file(project_id, old_name, new_name, commit)

    async def add_legal_terms(self, project_id: str, commit: CommitCallback) -> dict:
        return await self._editor.add_legal_terms(project_id, commit)

    async def add_state(self, project_id: str, commit: CommitCallback) -> StatePayload:
        return await self._editor.add_state(project_id, commit)

    async def add_signal(self, project_id: str, commit: CommitCallback) -> SignalPayload:
        return await self._editor.add_signal(project_id, commit)

    async def add_action(self, project_id: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self._editor.add_action(project_id, state_name, commit)

    async def set_state_field(
        self, project_id: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self._editor.set_state_field(project_id, state_name, field, value, commit)

    async def set_action_field(
        self, project_id: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self._editor.set_action_field(project_id, state_name, action_name, field, value, commit)

    async def set_signal_field(
        self, project_id: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self._editor.set_signal_field(project_id, signal_name, field, value, commit)

    async def set_init_action_field(self, project_id: str, field: str, value, commit: CommitCallback):
        return await self._editor.set_init_action_field(project_id, field, value, commit)

    async def set_project_field(self, project_id: str, field: str, value, commit: CommitCallback) -> ProjectPayload:
        return await self._editor.set_project_field(project_id, field, value, commit)

    async def delete_state(self, project_id: str, state_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_state(project_id, state_name, commit)

    async def delete_action(self, project_id: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_action(project_id, state_name, action_name, commit)

    async def delete_signal(self, project_id: str, signal_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_signal(project_id, signal_name, commit)

    async def add_env_key(self, project_id: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self._editor.add_env_key(project_id, commit)

    async def set_env_key_field(
        self, project_id: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self._editor.set_env_key_field(project_id, env_key_name, field, value, commit)

    async def delete_env_key(self, project_id: str, env_key_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_env_key(project_id, env_key_name, commit)

    async def add_source(
        self, project_id: str, commit: CommitCallback, name_hint: str | None = None, content: bytes = b"",
        driver: str = "avance",
    ) -> SourcePayload:
        return await self._editor.add_source(project_id, commit, name_hint, content, driver=driver)

    async def set_source_field(
        self, project_id: str, source_name: str, field: str, value, commit: CommitCallback
    ) -> SourcePayload:
        return await self._editor.set_source_field(project_id, source_name, field, value, commit)

    async def delete_source(self, project_id: str, source_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_source(project_id, source_name, commit)

    async def reorder_actions(
        self, project_id: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self._editor.reorder_actions(project_id, state_name, action_name, position, commit)

    async def undo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self._editor.undo_project_file(project_id, file_name, content)

    async def redo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self._editor.redo_project_file(project_id, file_name, content)

    def clear_project_history(self, project_id: str) -> None:
        self._editor.clear_project_history(project_id)

    async def delete_project_file(self, project_id: str, file_name: str, commit: CommitCallback) -> None:
        await self._editor.delete_project_file(project_id, file_name, commit)

    # -- App Store --------------------------------------------------------

    def list_app_store_apps(self, username: str, search: str | None = None) -> list[dict]:
        apps = self._db.list_projects_for_app_store(username, search)
        for app in apps:
            app["icon_file"] = self._find_app_icon_file(app["id"])
            app["reactions_enabled"] = self._project_has_reactions(app["id"])
        return apps

    def _find_app_icon_file(self, project_id: str) -> str | None:
        revision = self.get_published_revision(project_id)
        for name in self._db.list_archives(project_id, revision=revision):
            if _ICON_FILE_RE.match(name):
                return name
        return None

    def _project_has_reactions(self, project_id: str) -> bool:
        automaton = self.get_automaton(project_id, self.get_published_revision(project_id))
        return any(automaton.reactions_enabled_for(state) for state in automaton.states.values())

    def install_app(self, username: str, project_id: str) -> None:
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"No such project: {project_id!r}")
        self._db.install_project(username, project_id)

    def uninstall_app(self, username: str, project_id: str) -> None:
        self._db.delete_sessions_by_username_and_project(username, project_id)
        self._db.uninstall_project(username, project_id)

    def get_app_session_summaries(self, username: str, project_id: str) -> list[dict]:
        return self._db.list_session_summaries_for_user_project(username, project_id)

    def get_app_store_file_content(self, project_id: str, file_name: str) -> tuple[bytes, str]:
        revision = self.get_published_revision(project_id)
        return self._editor.get_project_file_content_at_revision(project_id, file_name, revision)

    def get_app_store_preview_messages(self, project_id: str) -> list[dict] | None:
        session = self._db.get_first_imported_session(project_id)
        if session is None:
            return None
        return [
            {"id": m["id"], "role": m["role"], "content": m["content"], "timestamp": m["timestamp"]}
            for m in self._db.get_messages(session["id"])
        ]
