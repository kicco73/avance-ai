"""What the engine needs to know about a project: which automaton and
which state, whether the published revision still builds, and whether
this person has accepted the terms.

Everything a *person editing* a project needs — publishing, revisions,
files, the app store, invite links, the graph and the per-state views —
moved to avance_platform/platform_service.py. The two are facades over
the same collaborators (inspector, manager, editor, invites), which is
why those are public here: PlatformService is built from them rather
than building a second set over the same database.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from automaton.automaton import (
    Action, ActionPayload, Automaton, EnvKeyPayload, ProjectPayload, SignalPayload, SourcePayload,
    State, StatePayload,
)
from turn.sessions.session_manager import SessionManager
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
from .web_import_crawler import WebCrawler

if TYPE_CHECKING:
    from ai import AiService

__all__ = ["ProjectService", "CommitCallback"]

_ICON_FILE_RE = re.compile(r'^aspect/icon\.(png|jpe?g|gif|webp|svg)$', re.IGNORECASE)


class ProjectService(object):
    def __init__(
        self, 
        db: Db, 
        automaton_loader: AutomatonLoader,
        session_manager: SessionManager,
        ai_service: "AiService | None" = None,
        invite_valid_days: int = 7, invite_max_shares: int = 3, whatsapp_number: str | None = None,
        whatsapp_invite_prefix: str = "Invitation code: ",
    ) -> None:
        self.db = db
        self.ai_service = ai_service
        self.web_crawler = WebCrawler()
        session_export_manager = SessionExportManager(db)
        session_import_manager = SessionImportManager(db)
        self.automaton_loader = automaton_loader
        self.inspector = ProjectInspector(db, self.automaton_loader, ai_service)
        self.manager = ProjectManager(
            db, self.automaton_loader, self.inspector, session_export_manager, session_import_manager,
            session_manager
        )
        self.editor = ProjectEditor(db, self.automaton_loader, self.inspector, self.manager, ai_service)
        self.invites = InviteManager(db, invite_valid_days, invite_max_shares, whatsapp_number, whatsapp_invite_prefix)

    def get_active_project_id(self) -> str:
        return self.inspector.get_active_project_id()

    def get_published_revision(self, project_id: str) -> int:
        return self.inspector.get_published_revision(project_id)

    def get_draft_revision(self, project_id: str) -> int:
        return self.inspector.get_draft_revision(project_id)

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return self.inspector.legal_terms_pending(username, project_id, revision=self.get_published_revision(project_id))

    def get_legal_terms_status(self, username: str, project_id: str) -> dict:
        return self.inspector.get_legal_terms_status(username, project_id, revision=self.get_published_revision(project_id))

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self.inspector.get_automaton(project_id, revision)

    # XXX Compiled automaton requirement - do not touch.
    # XXX BuildService has just replaced this revision's package on disk,
    # and whatever is cached for it is the interpreted automaton it
    # supersedes — the loader itself is not a collaborator BuildService has.

    def invalidate_automaton(self, project_id: str, revision: int) -> None:
        self.automaton_loader.invalidate(project_id, revision)

    def get_active_automaton(self) -> Automaton:
        return self.inspector.get_active_automaton()

    def get_automaton_and_state(
        self, project_id: str, type: str = 'live', username: str | None = None
    ) -> tuple[Automaton, State]:
        return self.inspector.get_automaton_and_state(project_id, type, username)

    def get_active_automaton_and_state(self, username: str | None = None) -> tuple[Automaton, State]:
        return self.inspector.get_active_automaton_and_state(username)

    def get_automaton_for_session(self, session_id: int) -> Automaton:
        return self.inspector.get_automaton_for_session(session_id)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        return self.inspector.get_automaton_and_state_for_session(session_id)

    def get_automaton_and_state_for_observer(
        self, project_id: str, username: str
    ) -> tuple[Automaton, State] | None:
        return self.inspector.get_automaton_and_state_for_observer(project_id, username)

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        return self.inspector.apply_manual_action(action_name, session_id)

    def get_project_availability(self, project_id: str) -> tuple[bool, str | None]:
        return self.manager.get_project_availability(project_id)

    def recompute_availability(self, project_id: str) -> None:
        self.manager.recompute_availability(project_id)

    def recompute_all_availability(self) -> None:
        self.manager.recompute_all_availability()

    def register_availability_cascade(self) -> None:
        self.manager.register_availability_cascade()

    def accept_legal_terms(self, username: str, project_id: str) -> None:
        self.manager.accept_legal_terms(username, project_id)

    def validate_invite_for_registration(self, code: str | None):
        return self.invites.validate_for_registration(code)

    def redeem_invite(self, invite, user_id: str) -> None:
        self.invites.redeem(invite, user_id)

    def reset_test_sessions(self, project_id: str) -> None:
        self.manager.reset_test_sessions(project_id)

    # -- ProjectEditor / ProjectManager: editing a project ---------------
    # Restored: these are operations *on a project*, and they stay with
    # the project. Only the design view calls them today, but that is a
    # fact about today's callers, not about what they are.

    async def activate_project_idempotent(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self.manager.activate_project_idempotent(project_id, commit)

    async def add_action(self, project_id: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self.editor.add_action(project_id, state_name, commit)

    async def add_env_key(self, project_id: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self.editor.add_env_key(project_id, commit)

    async def add_legal_terms(self, project_id: str, commit: CommitCallback) -> dict:
        return await self.editor.add_legal_terms(project_id, commit)

    async def add_signal(self, project_id: str, commit: CommitCallback) -> SignalPayload:
        return await self.editor.add_signal(project_id, commit)

    async def add_source(
        self, project_id: str, commit: CommitCallback, name_hint: str | None = None, content: bytes = b"",
    ) -> SourcePayload:
        return await self.editor.add_source(project_id, commit, name_hint, content)

    async def add_state(self, project_id: str, commit: CommitCallback) -> StatePayload:
        return await self.editor.add_state(project_id, commit)

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        return await self.manager.create_new_project(commit)

    async def delete_action(self, project_id: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_action(project_id, state_name, action_name, commit)

    async def delete_env_key(self, project_id: str, env_key_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_env_key(project_id, env_key_name, commit)

    async def delete_project(self, project_id: str, commit: CommitCallback) -> None:
        await self.manager.delete_project(project_id, commit)

    async def delete_project_file(self, project_id: str, file_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_project_file(project_id, file_name, commit)

    async def delete_signal(self, project_id: str, signal_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_signal(project_id, signal_name, commit)

    async def delete_source(self, project_id: str, source_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_source(project_id, source_name, commit)

    async def delete_state(self, project_id: str, state_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_state(project_id, state_name, commit)

    async def generate_index_css_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self.editor.generate_index_css_ai_edit(project_id, instruction)

    async def generate_index_yml_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self.editor.generate_index_yml_ai_edit(project_id, instruction)

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        return await self.manager.put_project(content, content_type, commit)

    async def put_project_file(
        self, project_id: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        return await self.editor.put_project_file(project_id, file_name, content, content_type_header, commit)

    async def redo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self.editor.redo_project_file(project_id, file_name, content)

    async def rename_project_file(self, project_id: str, old_name: str, new_name: str, commit: CommitCallback) -> dict:
        return await self.editor.rename_project_file(project_id, old_name, new_name, commit)

    async def reorder_actions(
        self, project_id: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self.editor.reorder_actions(project_id, state_name, action_name, position, commit)

    async def revert_to_published(self, project_id: str, commit: CommitCallback) -> dict:
        return await self.manager.revert_to_published(project_id, commit)

    async def set_action_field(
        self, project_id: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self.editor.set_action_field(project_id, state_name, action_name, field, value, commit)

    async def set_env_key_field(
        self, project_id: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self.editor.set_env_key_field(project_id, env_key_name, field, value, commit)

    async def set_init_action_field(self, project_id: str, field: str, value, commit: CommitCallback):
        return await self.editor.set_init_action_field(project_id, field, value, commit)

    async def set_project_field(self, project_id: str, field: str, value, commit: CommitCallback) -> dict:
        return await self.editor.set_project_field(project_id, field, value, commit)

    async def set_service_level(
        self, project_id: str, service: str, level: str, commit: CommitCallback
    ) -> ProjectPayload:
        return await self.editor.set_service_level(project_id, service, level, commit)

    async def set_signal_field(
        self, project_id: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self.editor.set_signal_field(project_id, signal_name, field, value, commit)

    async def set_source_field(
        self, project_id: str, source_name: str, field: str, value, commit: CommitCallback
    ) -> SourcePayload:
        return await self.editor.set_source_field(project_id, source_name, field, value, commit)

    async def set_state_field(
        self, project_id: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self.editor.set_state_field(project_id, state_name, field, value, commit)

    async def undo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self.editor.undo_project_file(project_id, file_name, content)

    def get_identifier_registry(self, project_id: str) -> dict[str, dict[str, str]]:
        return self.inspector.get_identifier_registry(project_id)

    def ensure_project_not_broken(self, project_id: str) -> None:
        self.manager.ensure_project_not_broken(project_id)
