"""Everything a caller asks *about a project*: its identifiers and
metrics, one user's signals and timeline, the sessions on it, and the
legal terms a person has to accept before a turn can run.

Core, not platform. These are questions about a project rather than about
the authoring environment, and the terms in particular gate every
channel — WhatsApp reads them through the turn service, and a product
with no editor still has to be able to answer them.
"""
from __future__ import annotations

import hashlib
from http import HTTPStatus

from fastapi import HTTPException, Request, Response

from automaton.automaton import Automaton
from automaton.file_types import ProjectFileTypes
from automaton.build_error import AutomatonBuildError
from auth.roles import role_satisfies
from controllers.base_controller import BaseController, get, post
from system.session import Session
from project.project_service import ProjectService
from turn.turn_service import TurnService


class ProjectController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

    @get("/api/core/projects")
    def get_projects(self):
        """Which projects this caller can see, and which one is active.
        Every build answers it: a session has to know what it is talking
        about before it can talk, whether or not an editor was installed
        to change it."""
        username = None if role_satisfies(Session().role, "supervisor") else Session().user
        return self.project_service.inspector.list_projects(username)

    @post("/api/core/projects/{project_id}/activate")
    async def activate_project(self, project_id: str):
        """Makes `project_id` the one this server answers turns for."""
        try:
            await self.project_service.activate_project_idempotent(project_id, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True, "project_id": project_id}

    @post("/api/core/projects/invitations/{code}", role="user")
    def post_resolve_invite_code(self, code: str):
        """Resolves a "share project" invite code back to the project it
        was generated for — the lookup a scanned QR/link needs right after
        login, before the visiting identity's role is known. A POST, not a
        GET: for a user reaching a project for the first time it also
        consumes the invite and grants access (see InviteManager.
        resolve_invite_link), which can fail. null project_id when the
        code resolves to nothing, never an error.

        Core because the link is the product's own front door: somebody
        opening a shared conversation has no editor in the picture."""
        try:
            project_id = self.project_service.invites.resolve_invite_link(code, Session().user, Session().role)
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        return {"project_id": project_id}

    async def _activate_project(self, project_id: str, new_automaton: Automaton) -> None:
        async with self.turn_service.acquire_write(project_id):
            pass

    @get("/api/core/projects/{project_id}/files/{file_name:path}/content")
    def get_project_file_content(self, project_id: str, file_name: str, request: Request, session_id: int | None = None):
        """Raw bytes of `file_name`'s content, for callers that can't use
        the JSON GET the editor uses. ETag'd off the content itself, so
        an unchanged file 304s on a matching If-None-Match.

        Core, and no elevated role: chatSkin.js's own loadSkin (index.css
        and any image it references, via cssAssetUrls.js) hits this for
        every live chat session, whoever is looking. A product built
        without an editor still has to be able to draw itself."""
        try:
            content, content_type = self.project_service.editor.get_project_file_content(project_id, file_name, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except AutomatonBuildError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return Response(
            content=content, media_type=content_type, headers={"ETag": etag, "Cache-Control": "no-cache"}
        )

    @get("/api/core/projects/{project_id}/states/{state_name}/tokens", role="supervisor")
    def get_state_input_tokens(self, project_id: str, state_name: str, session_id: int | None = None):
        """Estimated input-token cost of `state_name`'s own turn prompt,
        for the Inspect panel's detail card — fetched on demand for the
        one state currently open, not for the whole graph at once (see
        ProjectInspector.get_state_input_tokens). `tokens` is null when no
        AiService is configured for this deployment."""
        try:
            return {"tokens": self.project_service.inspector.get_state_input_tokens(project_id, state_name, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except AutomatonBuildError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/core/projects/{project_id}/signals", role="supervisor")
    def get_project_signals(self, project_id: str, state_key: str | None = None, session_id: int | None = None):
        """Signal definitions for the Inspect panel. `state_key`, when
        given, scopes each signal's `relevant` field to that state's
        outgoing actions. `session_id`: see get_project_graph.

        Core: a signal definition describes what the engine tracks, which
        every build does; the benchmark reads these too."""
        self.project_service.ensure_project_not_broken(project_id)
        try:
            return {"signals": self.project_service.inspector.get_project_signals(project_id, state_key, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except AutomatonBuildError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/core/projects/{project_id}/states", role="supervisor")
    def get_project_states(self, project_id: str):
        """Every real state key of `project_id`'s current draft
        automaton — the "States" branch's own node list (see
        TestsTree.vue)."""
        self.project_service.ensure_project_not_broken(project_id)
        try:
            return self.project_service.inspector.get_project_states(project_id)
        except AutomatonBuildError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/core/projects/file-types")
    def get_project_file_types(self):
        """Every file type a project can carry — extension, stored content
        type, UI label, kind, folder and upload limit — so the file
        explorer never has to restate them.

        Core: it is a static description of what a project may carry (see
        automaton/file_types.py), and the chat reads a project's files
        too."""
        return ProjectFileTypes.catalog_payload()

    @get("/api/core/projects/{project_id}/identifiers")
    def get_identifiers(self, project_id: str):
        """`project_id`'s own identifier registry — every identifier a
        trigger/`env:` expression can reference, one {identifier:
        description} dict per namespace."""
        self.project_service.ensure_project_not_broken(project_id)
        try:
            return self.project_service.get_identifier_registry(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/core/projects/{project_id}/metrics")
    def get_metrics(self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None):
        """Core metrics for `project_id`, live or (`message_id` given)
        as of that exact message — no caching. `full`: every core metric,
        including ones that need more than one session (e.g. Retention),
        instead of the usual "one_session" subset. `username` (omitted:
        the caller's own sessions): Manage Users' statistics panel, to
        inspect a specific user's sessions rather than its own. TurnServiceError
        for an unknown message_id is handled globally, see error_handlers.py."""
        return self.turn_service.get_metrics(
            project_id=project_id, message_id=message_id, full=full, username=username,
        )

    @get("/api/core/projects/{project_id}/users/{username}/latest-signals")
    def get_user_latest_signals(self, project_id: str, username: str):
        """The most recent live session's own latest signal snapshot for
        `username` in `project_id` — Manage Users' Signals tab."""
        return self.turn_service.get_latest_signal_values(project_id, username)

    @get("/api/core/projects/{project_id}/users/{username}/timeline")
    def get_user_timeline(self, project_id: str, username: str):
        return self.turn_service.get_timeline(project_id, username)

    @get("/api/core/projects/{project_id}/users/{username}/metrics-history")
    def get_user_metrics_history(self, project_id: str, username: str):
        return self.turn_service.get_metrics_history(project_id, username)

    @get("/api/core/projects/{project_id}/legal-terms/status")
    def get_legal_terms_status(self, project_id: str):
        return self.turn_service.get_legal_terms_status(project_id)

    @post("/api/core/projects/{project_id}/legal-terms/acceptance")
    def post_accept_chat_terms(self, project_id: str):
        self.turn_service.accept_legal_terms(project_id)
        return {"success": True}

    @get("/api/core/projects/{project_id}/sessions")
    def get_sessions(self, project_id: str, include_imported: bool = False):
        """Every session for `project_id`, for the "Sessions" side
        panel — see TurnService.list_sessions."""
        return self.turn_service.list_sessions(include_imported=include_imported, project_id=project_id)
