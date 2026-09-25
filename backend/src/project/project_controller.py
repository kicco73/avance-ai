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

from automaton.file_types import ProjectFileTypes
from auth.roles import role_satisfies
from controllers.base_controller import BaseController, delete, get, post
from schemas import SaveMediaToDriveRequest
from system import bus
from system.bus import OUTPUT_DRIVE, Message
from system.web_session import WebSession
from project.project_service import ProjectService
from turn.turn_service import TurnService


class ProjectController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

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
            project_id = self.project_service.invites.resolve_invite_link(code, WebSession().user, WebSession().role)
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        return {"project_id": project_id}

    @get("/api/core/projects/{project_id}/files/{file_name:path}/content")
    def get_project_file_content(self, project_id: str, file_name: str, request: Request, session_id: int | None = None):
        """Raw bytes of `file_name`'s content, for callers that can't use
        the JSON GET the editor uses. ETag'd off the content itself, so
        an unchanged file 304s on a matching If-None-Match.

        Core, and no elevated role: chatSkin.js's own loadSkin (index.css
        and any image it references, via cssAssetUrls.js) hits this for
        every live chat session, whoever is looking. A product built
        without an editor still has to be able to draw itself."""
        content, content_type = self.project_service.editor.get_project_file_content(project_id, file_name, session_id)
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return Response(
            content=content, media_type=content_type, headers={"ETag": etag, "Cache-Control": "no-cache"}
        )

    @get("/api/core/projects/{project_id}/drive")
    def get_drive_files(self, project_id: str, prefix: str = ""):
        """The caller's own drive files in `project_id` (see
        tracking.actuators.drive_namespace) — never anyone else's, and
        no elevated role: a drive belongs to the person, not to the
        project's authors."""
        return {"files": self.project_service.list_drive_files(project_id, WebSession().user, prefix)}

    @get("/api/core/projects/{project_id}/drive/{path:path}")
    def get_drive_file_content(self, project_id: str, path: str):
        found = self.project_service.read_drive_file(project_id, WebSession().user, path)
        if found is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"'{path}' not found in your drive.")
        content, content_type = found
        return Response(content=content, media_type=content_type)

    @delete("/api/core/projects/{project_id}/drive/{path:path}", role="customer")
    async def delete_drive_file(self, project_id: str, path: str):
        if not self.project_service.delete_drive_file(project_id, WebSession().user, path):
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"'{path}' not found in your drive.")
        await bus.publish(Message(
            type=OUTPUT_DRIVE, username=WebSession().user, project_id=project_id,
            session_id=None, body={"path": path},
        ))
        return {"path": path}

    @post("/api/core/projects/{project_id}/drive/save-media", role="customer")
    async def post_save_media_to_drive(self, project_id: str, req: SaveMediaToDriveRequest):
        """Adds one of this project's own media/ files to the caller's
        own drive, unchanged if it's already there — the Save button in
        the PDF preview dialog. role="customer": the same floor drive
        access itself is gated at everywhere else in the frontend."""
        downloads = self.project_service.save_media_to_drive(project_id, WebSession().user, req.file_name)
        await bus.publish(Message(
            type=OUTPUT_DRIVE, username=WebSession().user, project_id=project_id,
            session_id=None, body={"path": req.file_name},
        ))
        return {"path": req.file_name, "downloads": downloads}

    @post("/api/core/projects/{project_id}/drive/download-media", role="customer")
    async def post_download_media_to_drive(self, project_id: str, req: SaveMediaToDriveRequest):
        """Same as post_save_media_to_drive, but also counts as a
        download — the Download button in the PDF preview dialog, always
        shown (unlike Save, which hides once the file is already there),
        since every click is one more download. The count is what
        drive.downloads(path) reads back."""
        downloads = self.project_service.save_media_to_drive(
            project_id, WebSession().user, req.file_name, count_download=True,
        )
        await bus.publish(Message(
            type=OUTPUT_DRIVE, username=WebSession().user, project_id=project_id,
            session_id=None, body={"path": req.file_name},
        ))
        return {"path": req.file_name, "downloads": downloads}

    @post("/api/core/projects/{project_id}/drive/{path:path}/download", role="customer")
    async def post_record_drive_download(self, project_id: str, path: str):
        """Bumps the downloads counter for a file already in the caller's
        own drive — the Download button shown while viewing a file from
        CustomerHome's Drive tab. Distinct from GET .../drive/{path},
        which is how that same file gets viewed, and doesn't count."""
        downloads = self.project_service.record_drive_download(project_id, WebSession().user, path)
        await bus.publish(Message(
            type=OUTPUT_DRIVE, username=WebSession().user, project_id=project_id,
            session_id=None, body={"path": path},
        ))
        return {"path": path, "downloads": downloads}

    @get("/api/core/projects/{project_id}/states/{state_name}/tokens", role="supervisor")
    def get_state_input_tokens(self, project_id: str, state_name: str, session_id: int | None = None):
        """Estimated input-token cost of `state_name`'s own turn prompt,
        for the Inspect panel's detail card — fetched on demand for the
        one state currently open, not for the whole graph at once (see
        ProjectInspector.get_state_input_tokens). `tokens` is null when no
        AiService is configured for this deployment."""
        return {"tokens": self.project_service.inspector.get_state_input_tokens(project_id, state_name, session_id)}

    @get("/api/core/projects/{project_id}/signals", role="supervisor")
    def get_project_signals(self, project_id: str, state_key: str | None = None, session_id: int | None = None):
        """Signal definitions for the Inspect panel. `state_key`, when
        given, scopes each signal's `relevant` field to what a turn in
        that state computes (its `signal-tracking-strategy`). `session_id`: see get_project_graph.

        Core: a signal definition describes what the engine tracks, which
        every build does; the benchmark reads these too."""
        self.project_service.ensure_project_not_broken(project_id)
        return {"signals": self.project_service.inspector.get_project_signals(project_id, state_key, session_id)}

    @get("/api/core/projects/{project_id}/states", role="supervisor")
    def get_project_states(self, project_id: str):
        """Every real state key of `project_id`'s current draft
        automaton — the "States" branch's own node list (see
        TestsTree.vue)."""
        self.project_service.ensure_project_not_broken(project_id)
        return self.project_service.inspector.get_project_states(project_id)

    @post("/api/core/projects/{project_id}/activate", role="user")
    async def activate_project(self, project_id: str):
        """Makes `project_id` the one this server answers turns for.

        Core, not the authoring skill it used to live in: which project is
        active is what decides the conversation a person is having (see
        TurnService.get_current_session_if_any_or_create_new, which reads
        it and takes nothing from the client), and the invite flow
        activates the project it just redeemed in every build, editor or
        not. The frontend has always called it here."""
        await self.project_service.activate_project_idempotent(project_id)
        return {"success": True, "project_id": project_id}

    @get("/api/core/projects")
    def get_projects(self):
        """Which projects this caller can see, and which one is active.
        Every build answers it: a session has to know what it is talking
        about before it can talk, whether or not an editor was installed
        to change it."""
        username = None if role_satisfies(WebSession().role, "supervisor") else WebSession().user
        return self.project_service.inspector.list_projects(username)

    @get("/api/core/projects/subscribed")
    def get_subscribed_projects(self):
        """Which projects the caller is subscribed to, ignoring role.

        get_projects above lets a supervisor/admin see every project, which
        is right for the editor's own project switcher (LabelProjectView) —
        but LiveChat's app menu (ProjectsMenu.vue with subscribed-only) is
        the caller's own subscription list even for an elevated account."""
        return self.project_service.inspector.list_projects(WebSession().user)

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
        return self.project_service.get_identifier_registry(project_id)

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
