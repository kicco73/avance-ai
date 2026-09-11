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

import asyncio
import re
from typing import TYPE_CHECKING

from automaton.automaton import Automaton, CompiledAutomaton, ProjectPayload, StatePayload
from project.web_import_job import WebImportJob
from tracking.sources.url import parse_source_url

if TYPE_CHECKING:
    from project.project_service import CommitCallback, ProjectService

_ICON_FILE_RE = re.compile(r'^aspect/icon\.(png|jpe?g|gif|webp|svg)$', re.IGNORECASE)


class PlatformService(object):

    def __init__(self, project_service: "ProjectService") -> None:
        # The project service itself, not its parts. Some of these
        # methods also ask a question *about the project* — which
        # revision is published, what its automaton is — and those are
        # asked of it rather than reimplemented here.
        self.project_service = project_service
        # Set by install(); a service that was built only to answer
        # questions (as the tests build it) never has any.
        self.controllers: list = []

    def install(self, core: dict, controllers: list) -> None:
        """Builds this service's own controllers and adds them to the
        router's list — the same shape whatsapp/skill.py's `_WhatsApp`
        uses, and the reason the skill no longer knows seven controller
        signatures. `core` is bus.POINT_CORE_SERVICES' registry, read
        here and not kept: nothing below needs it after construction."""
        from avance_platform.app_store_controller import AppStoreController
        from avance_platform.auth_controller import AuthController
        from avance_platform.edit_project_controller import EditProjectController
        from avance_platform.label_project_controller import LabelProjectController
        from avance_platform.platform_controller import PlatformController
        from avance_platform.settings_controller import SettingsController
        from avance_platform.user_controller import UserController

        turn_service = core["turn_service"]
        project_service = core["project_service"]
        scheduler_service = core["scheduler_service"]
        auth_service = core["auth_service"]

        self.controllers = [
            PlatformController(turn_service, project_service, self),
            EditProjectController(turn_service, project_service, self, scheduler_service),
            # Labelling only: the benchmark half of that screen left with
            # the package that runs it (see testing/testing_controller.py),
            # so a build without benchmarking still annotates sessions.
            LabelProjectController(
                turn_service, self, core["tracking_service"], scheduler_service,
            ),
            SettingsController(
                turn_service, project_service, self, core["db"], core["version"],
                scheduler_service, core["services_config"],
            ),
            AuthController(auth_service),
            UserController(auth_service),
            AppStoreController(turn_service, self),
        ]
        controllers.extend(self.controllers)

    # The collaborators are read through, never copied. Copying them in
    # __init__ made this a snapshot: replacing project_service.ai_service
    # or .web_crawler afterwards (which is exactly what the web-import
    # tests do, and what any substitution at runtime would do) left this
    # facade holding the originals, so a job built here used the real
    # crawler while the caller believed it had installed a fake. Two
    # facades over *one* set of collaborators was the whole point; these
    # properties are what make that true rather than true-at-construction.

    @property
    def db(self):
        return self.project_service.db

    @property
    def ai_service(self):
        return self.project_service.ai_service

    @property
    def web_crawler(self):
        return self.project_service.web_crawler

    @property
    def inspector(self):
        return self.project_service.inspector

    @property
    def manager(self):
        return self.project_service.manager

    @property
    def editor(self):
        return self.project_service.editor

    @property
    def invites(self):
        return self.project_service.invites

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

    async def activate_project(self, project_id: str, commit: CommitCallback) -> Automaton:
        return await self.manager.activate_project(project_id, commit)

    def export_project_zip(self, project_id: str) -> bytes:
        return self.manager.export_project_zip(project_id)

    def list_project_files(self, project_id: str) -> list[str]:
        return self.editor.list_project_files(project_id)

    def get_project_file(self, project_id: str, file_name: str) -> dict:
        return self.editor.get_project_file(project_id, file_name)

    def get_project_file_content(
        self, project_id: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        return self.editor.get_project_file_content(project_id, file_name, session_id)

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

    def clear_project_history(self, project_id: str) -> None:
        self.editor.clear_project_history(project_id)

    def list_app_store_apps(self, username: str, search: str | None = None) -> list[dict]:
        apps = self.db.list_projects_for_app_store(username, search)
        for app in apps:
            app["icon_file"] = self._find_app_icon_file(app["id"])
            automaton = self.project_service.get_automaton(app["id"], self.project_service.get_published_revision(app["id"]))
            app["reactions_enabled"] = any(automaton.reactions_enabled_for(s) for s in automaton.states.values())
            app["compiled"] = isinstance(automaton, CompiledAutomaton)
        return apps

    def _find_app_icon_file(self, project_id: str) -> str | None:
        revision = self.project_service.get_published_revision(project_id)
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
        revision = self.project_service.get_published_revision(project_id)
        return self.editor.get_project_file_content_at_revision(project_id, file_name, revision)

    def get_app_store_preview_messages(self, project_id: str) -> list[dict] | None:
        session = self.db.get_first_imported_session(project_id)
        if session is None:
            return None
        return [
            {"id": m["id"], "role": m["role"], "content": m["content"], "timestamp": m["timestamp"]}
            for m in self.db.get_messages(session["id"])
        ]
