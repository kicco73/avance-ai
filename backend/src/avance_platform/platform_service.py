"""Everything a person editing a project asks for: publishing and
revisions, the project's own files and graph and per-state views, the
app store, invite links, and the runtime switches an administrator
throws.

Split out of ProjectService, which had grown to fifty-nine methods
because it was written when there was only one caller. What is left
there is what the engine needs at turn time; what is here has no meaning
in a product with no editor, and is not in a build that leaves
src/avance_platform/ out.

Built from ProjectService's own collaborators rather than from a second
set of its own: two facades over one inspector, one manager, one editor
and one invite manager, so a revision published through this one is
immediately what the engine loads through the other.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project.project_service import CommitCallback, ProjectService

_ICON_FILE_RE = re.compile(r'^aspect/icon\.(png|jpe?g|gif|webp|svg)$', re.IGNORECASE)


class PlatformService(object):

    def __init__(self, project_service: "ProjectService") -> None:
        self.db = project_service.db
        self.ai_service = project_service.ai_service
        self.web_crawler = project_service.web_crawler
        self.inspector = project_service.inspector
        self.manager = project_service.manager
        self.editor = project_service.editor
        self.invites = project_service.invites

    def get_active_state_payload(self) -> StatePayload:
        return self.inspector.get_active_state_payload()

    def get_project_signals(
        self, project_id: str, state_key: str | None = None, session_id: int | None = None
    ) -> list[dict]:
        return self.inspector.get_project_signals(project_id, state_key, session_id)

    def get_project_env_keys(self, project_id: str, session_id: int | None = None) -> list[dict]:
        return self.inspector.get_project_env_keys(project_id, session_id)

    def get_project_sources(self, project_id: str, session_id: int | None = None) -> list[dict]:
        return self.inspector.get_project_sources(project_id, session_id)

    def get_project_metadata(self, project_id: str) -> ProjectPayload:
        return self.inspector.get_project_metadata(project_id)

    def get_identifier_registry(self, project_id: str) -> dict[str, dict[str, str]]:
        return self.inspector.get_identifier_registry(project_id)

    def get_project_states(self, project_id: str) -> list[str]:
        return self.inspector.get_project_states(project_id)

    def get_project_graph(self, project_id: str, session_id: int | None = None) -> dict:
        return self.inspector.get_project_graph(project_id, session_id)

    def get_state_input_tokens(self, project_id: str, state_key: str, session_id: int | None = None) -> int | None:
        return self.inspector.get_state_input_tokens(project_id, state_key, session_id)

    def list_projects(self, username: str | None = None) -> dict:
        return self.inspector.list_projects(username)

    def get_project_revision_info(self, project_id: str) -> dict:
        return self.inspector.get_project_revision_info(project_id)

    def ensure_project_not_broken(self, project_id: str) -> None:
        self.manager.ensure_project_not_broken(project_id)

    def get_runtime_status(self) -> list[dict]:
        return self.manager.get_runtime_status()

    def create_invite(self, project_id: str, created_by: str | None) -> dict:
        return self.invites.create_invite(project_id, created_by)

    def resolve_invite_link(self, code: str | None, user_id: str, role: str) -> str | None:
        return self.invites.resolve_invite_link(code, user_id, role)

    def set_manually_paused(self, project_id: str) -> dict:
        return self.manager.set_manually_paused(project_id)

    def set_manually_running(self, project_id: str) -> dict:
        return self.manager.set_manually_running(project_id)

    def get_project_runtime_status(self, project_id: str) -> dict:
        return self.manager.get_project_runtime_status(project_id)

    def wipe_all_live_sessions(self) -> None:
        self.manager.wipe_all_live_sessions()

    def clean_unused_revisions(self) -> int:
        return self.manager.clean_unused_revisions()

    def preview_publish(self, project_id: str) -> dict:
        return self.manager.preview_publish(project_id)

    def publish_project(self, project_id: str, remap_to: str | None = None) -> dict:
        return self.manager.publish_project(project_id, remap_to)

    async def revert_to_published(self, project_id: str, commit: CommitCallback) -> dict:
        return await self.manager.revert_to_published(project_id, commit)

    async def activate_project(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self.manager.activate_project(project_id, commit)

    async def activate_project_idempotent(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self.manager.activate_project_idempotent(project_id, commit)

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        return await self.manager.put_project(content, content_type, commit)

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        return await self.manager.create_new_project(commit)

    def export_project_zip(self, project_id: str) -> bytes:
        return self.manager.export_project_zip(project_id)

    async def delete_project(self, project_id: str, commit: CommitCallback) -> None:
        await self.manager.delete_project(project_id, commit)

    def list_project_files(self, project_id: str) -> list[str]:
        return self.editor.list_project_files(project_id)

    def get_project_file(self, project_id: str, file_name: str) -> dict:
        return self.editor.get_project_file(project_id, file_name)

    async def generate_index_yml_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self.editor.generate_index_yml_ai_edit(project_id, instruction)

    async def generate_index_css_ai_edit(self, project_id: str, instruction: str) -> str:
        return await self.editor.generate_index_css_ai_edit(project_id, instruction)

    def get_project_file_content(
        self, project_id: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        return self.editor.get_project_file_content(project_id, file_name, session_id)

    async def put_project_file(
        self, project_id: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        return await self.editor.put_project_file(project_id, file_name, content, content_type_header, commit)

    async def rename_project_file(self, project_id: str, old_name: str, new_name: str, commit: CommitCallback) -> dict:
        return await self.editor.rename_project_file(project_id, old_name, new_name, commit)

    async def add_legal_terms(self, project_id: str, commit: CommitCallback) -> dict:
        return await self.editor.add_legal_terms(project_id, commit)

    async def add_state(self, project_id: str, commit: CommitCallback) -> StatePayload:
        return await self.editor.add_state(project_id, commit)

    async def add_signal(self, project_id: str, commit: CommitCallback) -> SignalPayload:
        return await self.editor.add_signal(project_id, commit)

    async def add_action(self, project_id: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self.editor.add_action(project_id, state_name, commit)

    async def set_state_field(
        self, project_id: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self.editor.set_state_field(project_id, state_name, field, value, commit)

    async def set_action_field(
        self, project_id: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self.editor.set_action_field(project_id, state_name, action_name, field, value, commit)

    async def set_signal_field(
        self, project_id: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self.editor.set_signal_field(project_id, signal_name, field, value, commit)

    async def set_init_action_field(self, project_id: str, field: str, value, commit: CommitCallback):
        return await self.editor.set_init_action_field(project_id, field, value, commit)

    async def set_project_field(self, project_id: str, field: str, value, commit: CommitCallback) -> ProjectPayload:
        return await self.editor.set_project_field(project_id, field, value, commit)

    async def delete_state(self, project_id: str, state_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_state(project_id, state_name, commit)

    async def delete_action(self, project_id: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_action(project_id, state_name, action_name, commit)

    async def delete_signal(self, project_id: str, signal_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_signal(project_id, signal_name, commit)

    async def add_env_key(self, project_id: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self.editor.add_env_key(project_id, commit)

    async def set_env_key_field(
        self, project_id: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self.editor.set_env_key_field(project_id, env_key_name, field, value, commit)

    async def delete_env_key(self, project_id: str, env_key_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_env_key(project_id, env_key_name, commit)

    async def add_source(
        self, project_id: str, commit: CommitCallback, name_hint: str | None = None, content: bytes = b"",
    ) -> SourcePayload:
        return await self.editor.add_source(project_id, commit, name_hint, content)

    async def set_source_field(
        self, project_id: str, source_name: str, field: str, value, commit: CommitCallback
    ) -> SourcePayload:
        return await self.editor.set_source_field(project_id, source_name, field, value, commit)

    async def delete_source(self, project_id: str, source_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_source(project_id, source_name, commit)

    def build_web_import_job(
        self, project_id: str, source_name: str, query: str, commit: CommitCallback,
    ) -> WebImportJob:
        query = (query or "").strip()
        if not query:
            raise ValueError("A search query is required.")
        if self.ai_service is None:
            raise ValueError("No AI service is configured — an AI web import needs one.")
        return WebImportJob(
            self.editor, self.ai_service, self.web_crawler, asyncio.get_running_loop(),
            project_id, source_name, self._source_archive_name(project_id, source_name), query, commit,
        )

    def _source_archive_name(self, project_id: str, source_name: str) -> str:
        for entry in self.get_project_sources(project_id):
            source = entry["source"]
            if source["name"] != source_name:
                continue
            scheme, path = parse_source_url(source["url"])
            if scheme != "avance" or path == "env":
                raise ValueError(f"Source '{source_name}' has no CSV archive to import into.")
            return path
        raise FileNotFoundError(f"Source '{source_name}' does not exist in project '{project_id}'.")

    async def reorder_actions(
        self, project_id: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self.editor.reorder_actions(project_id, state_name, action_name, position, commit)

    async def undo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self.editor.undo_project_file(project_id, file_name, content)

    async def redo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        return await self.editor.redo_project_file(project_id, file_name, content)

    def clear_project_history(self, project_id: str) -> None:
        self.editor.clear_project_history(project_id)

    async def delete_project_file(self, project_id: str, file_name: str, commit: CommitCallback) -> None:
        await self.editor.delete_project_file(project_id, file_name, commit)

    def list_app_store_apps(self, username: str, search: str | None = None) -> list[dict]:
        apps = self.db.list_projects_for_app_store(username, search)
        for app in apps:
            app["icon_file"] = self._find_app_icon_file(app["id"])
            automaton = self.get_automaton(app["id"], self.get_published_revision(app["id"]))
            app["reactions_enabled"] = any(automaton.reactions_enabled_for(s) for s in automaton.states.values())
            app["compiled"] = isinstance(automaton, CompiledAutomaton)
        return apps

    def _find_app_icon_file(self, project_id: str) -> str | None:
        revision = self.get_published_revision(project_id)
        for name in self.db.list_archives(project_id, revision=revision):
            if _ICON_FILE_RE.match(name):
                return name
        return None

    def install_app(self, username: str, project_id: str) -> None:
        if not self.db.project_exists(project_id):
            raise FileNotFoundError(f"No such project: {project_id!r}")
        self.db.install_project(username, project_id)

    def uninstall_app(self, username: str, project_id: str) -> None:
        self.db.delete_sessions_by_username_and_project(username, project_id)
        self.db.uninstall_project(username, project_id)

    def get_app_session_summaries(self, username: str, project_id: str) -> list[dict]:
        return self.db.list_session_summaries_for_user_project(username, project_id)

    def get_app_store_file_content(self, project_id: str, file_name: str) -> tuple[bytes, str]:
        revision = self.get_published_revision(project_id)
        return self.editor.get_project_file_content_at_revision(project_id, file_name, revision)

    def get_app_store_preview_messages(self, project_id: str) -> list[dict] | None:
        session = self.db.get_first_imported_session(project_id)
        if session is None:
            return None
        return [
            {"id": m["id"], "role": m["role"], "content": m["content"], "timestamp": m["timestamp"]}
            for m in self.db.get_messages(session["id"])
        ]
