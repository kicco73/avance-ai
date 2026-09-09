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

import asyncio
import re
from typing import TYPE_CHECKING

from automaton.automaton import (
    Action, ActionPayload, Automaton, CompiledAutomaton, EnvKeyPayload, ProjectPayload, SignalPayload, SourcePayload,
    State, StatePayload,
)
from turn.sessions.session_manager import SessionManager
from db import Db
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager
from tracking.sources.url import parse_source_url

from .editor import ProjectEditor
from .inspector import ProjectInspector
from .invites import InviteManager
from .manager import ProjectManager
from .archive.automaton_loader import AutomatonLoader
from .project_import_bundle_job import ProjectImportBundleJob
from .types import CommitCallback
from .web_import_crawler import WebCrawler
from .web_import_job import WebImportJob

if TYPE_CHECKING:
    from ai import AiService

    # Type-only: naming it here costs this module no runtime dependency on
    # the compiled-automaton path, which it never chooses (see main.py).
    from .archive.compiled_automaton_loader import CompiledAutomatonLoader

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
