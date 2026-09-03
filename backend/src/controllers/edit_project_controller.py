"""EditProjectView.vue's own backend surface ("Edit project") — the
embedded "Test" chat, every read/edit endpoint behind the Inspector
(States/Actions/Signals/Env/Info) and the file explorer, and the
publish/revert lifecycle of one project's own draft.
"""
from __future__ import annotations

import hashlib
from http import HTTPStatus

from fastapi import HTTPException, Request, Response

from automaton.automaton_yaml_editor import InitActionTargetError
from chat.chat_service import ChatService
from project.project_service import ProjectService
from schemas import AiEditRequest, PublishProjectRequest, ReorderActionRequest, SetProjectFieldRequest
from session import Session

from .base_controller import BaseController, delete, get, post, put
from .project_commit_mixin import ProjectCommitMixin

# Explicit per-type whitelists for the field-by-field edit endpoints
# below — name/key is deliberately never in any of these three: it's
# generated once at creation and immutable from then on.
STATE_EDITABLE_FIELDS = {"ui-label", "ui-description", "history-cutoff", "contextual-prompt", "chat", "reactions-enabled"}
ACTION_EDITABLE_FIELDS = {"ui-label", "ui-description", "action-prompt", "target", "trigger", "on-enter", "env"}
# The init-action is an action like any other (see AutomatonYamlEditor.
# _init_action_payload) minus 'trigger' — it's the automaton's
# unconditional entry point, never conditionally fired, so
# AutomatonBuilder's own _build_init_action never reads that field for
# it and this endpoint refuses to write one that would just sit dead in
# the YAML. 'env' stays editable: AutomatonBuilder._build_init_action
# merges it on top of every declared env key's own default.
INIT_ACTION_EDITABLE_FIELDS = ACTION_EDITABLE_FIELDS - {"trigger"}
SIGNAL_EDITABLE_FIELDS = {"ui-label", "ui-description", "definition"}
# Unlike a state/action/signal, an env key has no separate ui-label to
# derive its name from — 'name' is itself directly editable here.
ENV_KEY_EDITABLE_FIELDS = {"name", "ui-description", "value"}
# The optional top-level `project:` section — 'id' is what other
# projects reach this one as through automaton.<id>. 'general-prompt' is
# actually its own separate top-level key (see AutomatonYamlEditor.
# set_project_field), grouped in here only because the frontend edits it
# from the same Project card.
PROJECT_EDITABLE_FIELDS = {"id", "ui-label", "ui-description", "talk-enabled", "signal-tracking-on-ai-message", "general-prompt"}


class EditProjectController(BaseController, ProjectCommitMixin):

    def __init__(self, chat_service: ChatService, project_service: ProjectService) -> None:
        self.chat_service = chat_service
        self.project_service = project_service

    @post("/api/projects/{project_id}/test-sessions", role="admin")
    async def post_create_test_session(self, project_id: str):
        """The embedded "Test" chat's explicit "start a new session"
        action — the one place a session may exist against an unpublished
        revision."""
        return await self.chat_service.create_draft_session(project_id)

    @get("/api/projects/{project_id}/test-sessions/current", role="admin")
    async def get_current_test_session(self, project_id: str, session_id: int | None = None):
        """The embedded "Test" chat's bootstrap endpoint — the
        draft-session equivalent of GET /api/chat/session."""
        return await self.chat_service.get_current_draft_session_if_any_or_create_new(session_id, project_id)

    @get("/api/projects/{project_id}/test-sessions", role="admin")
    def get_test_sessions(self, project_id: str):
        """The embedded "Test" chat's own "Sessions" panel listing — the
        draft-session equivalent of GET .../sessions. The two pools never mix."""
        return self.chat_service.list_test_sessions(project_id)

    @post("/api/projects/{project_id}/test-sessions/reset", role="admin")
    async def post_reset_test_sessions(self, project_id: str):
        async with self.chat_service.acquire_write(project_id):
            return self.chat_service.reset_test_sessions(project_id)

    @get("/api/projects/{project_id}/states", role="admin")
    def get_project_states(self, project_id: str):
        """Every real state key of `project_id`'s current draft
        automaton — the "States" branch's own node list (see
        TestsTree.vue)."""
        try:
            return self.project_service.get_project_states(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/graph", role="supervisor")
    def get_project_graph(self, project_id: str, session_id: int | None = None):
        """The project's state machine (states as nodes, actions as
        edges), for the Inspect panel graph. `session_id` omitted
        resolves the current draft; given, resolves that session's revision."""
        try:
            return self.project_service.get_project_graph(project_id, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/states/{state_name}/tokens", role="supervisor")
    def get_state_input_tokens(self, project_id: str, state_name: str, session_id: int | None = None):
        """Estimated input-token cost of `state_name`'s own turn prompt,
        for the Inspect panel's detail card — fetched on demand for the
        one state currently open, not for the whole graph at once (see
        ProjectInspector.get_state_input_tokens). `tokens` is null when no
        AiService is configured for this deployment."""
        try:
            return {"tokens": self.project_service.get_state_input_tokens(project_id, state_name, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/signals", role="supervisor")
    def get_project_signals(self, project_id: str, state_key: str | None = None, session_id: int | None = None):
        """Signal definitions for the Inspect panel. `state_key`, when
        given, scopes each signal's `relevant` field to that state's
        outgoing actions. `session_id`: see get_project_graph above."""
        try:
            return {"signals": self.project_service.get_project_signals(project_id, state_key, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/env-keys", role="admin")
    def get_project_env_keys(self, project_id: str, session_id: int | None = None):
        """Declared env-key definitions for the "Edit project" view's
        Inspect panel Env tab. `session_id`: see get_project_graph above."""
        try:
            return {"env_keys": self.project_service.get_project_env_keys(project_id, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/project", role="admin")
    def get_project_metadata(self, project_id: str):
        """The optional top-level `project:` section of `project_id`'s
        last saved index.yml, for the Inspect panel Info tab."""
        try:
            return {"project": self.project_service.get_project_metadata(project_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/invites", role="admin")
    def post_create_invite(self, project_id: str):
        """ShareProjectDialog.vue's own trigger — a fresh Invite row (own
        random code, expiry, max-shares budget — see
        ProjectService.create_invite) every time the dialog opens, never
        reused. {code, expires_at, max_shares, whatsapp_url}; whatsapp_url
        is null unless whatsapp-service is configured."""
        try:
            return self.project_service.create_invite(project_id, Session().user)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @post("/api/projects/by-invite/{code}", role="user")
    def post_resolve_invite_code(self, code: str):
        """Resolves a "share project" invite code back to the project it
        was generated for. Unlike every other route in this file, open to
        any authenticated role: it's the lookup a scanned QR/link needs
        right after login (see shareLink.js and useAppBoot.js), well
        before the visiting identity's own role is known to be admin. A
        POST, not a GET: for role="user" reaching a project for the first
        time, this also consumes the invite and grants access (see
        ProjectService.resolve_invite_link) — a real side effect, and one
        that can fail (expired/maxed-out link). null project_id when
        the code doesn't resolve to anything at all, never an error."""
        try:
            project_id = self.project_service.resolve_invite_link(code, Session().user, Session().role)
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        return {"project_id": project_id}

    @get("/api/projects/{project_id}/files", role="admin")
    def get_project_files(self, project_id: str):
        """Text-editable files inside `project_id`'s directory (index.yml
        plus any text attachments), for the "Edit project" view's file
        explorer panel."""
        try:
            return {"files": self.project_service.list_project_files(project_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/files/{file_name:path}/content")
    def get_project_file_content(self, project_id: str, file_name: str, request: Request, session_id: int | None = None):
        """Raw bytes of `file_name`'s content, for callers that can't use
        the JSON GET below. ETag'd off the content itself, so an
        unchanged file 304s on a matching If-None-Match. No elevated role:
        chatStore.js's own loadSkin (index.css + any image it references,
        via cssAssetUrls.js) hits this for every live chat session,
        regardless of the viewer's role."""
        try:
            content, content_type = self.project_service.get_project_file_content(project_id, file_name, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return Response(
            content=content, media_type=content_type, headers={"ETag": etag, "Cache-Control": "no-cache"}
        )

    # Named to sort alphabetically after get_project_file_content: routes
    # register in alphabetical method-name order, and this method's own
    # {file_name:path} wildcard (needed for legal/terms.md) would otherwise
    # swallow get_project_file_content's literal "/content" suffix.
    @get("/api/projects/{project_id}/files/{file_name:path}", role="admin")
    def get_project_file_info(self, project_id: str, file_name: str):
        """{content, can_undo, can_redo} of `file_name`'s current
        content — can_undo/can_redo drive the Undo/Redo buttons. Missing
        index.css reports 204 instead of 404, since it's optional."""
        try:
            return self.project_service.get_project_file(project_id, file_name)
        except FileNotFoundError as exc:
            if file_name == "index.css":
                return Response(status_code=HTTPStatus.NO_CONTENT)
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/files/{file_name:path}/undo", role="admin")
    async def undo_project_file(self, project_id: str, file_name: str, request: Request):
        """Loads a step back into the current user's undo history for
        `file_name` — a pure editor preview: nothing is persisted, and
        the active project is never reloaded; only Save does that."""
        content = await request.body()
        try:
            return await self.project_service.undo_project_file(project_id, file_name, content)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/files/{file_name:path}/redo", role="admin")
    async def redo_project_file(self, project_id: str, file_name: str, request: Request):
        """Mirror of .../undo, replaying the current user's own redo
        history instead (see ProjectService.redo_project_file)."""
        content = await request.body()
        try:
            return await self.project_service.redo_project_file(project_id, file_name, content)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/files/index.yml/ai-edit", role="admin")
    async def post_index_yml_ai_edit(self, project_id: str, req: AiEditRequest):
        """The index.yml editor's AI button: asks the configured AiService
        to rewrite index.yml per `req.instruction`, given the format spec
        and this project's current content — see ProjectEditor.
        generate_index_yml_ai_edit. A pure preview, like undo/redo above:
        nothing is persisted, the frontend drops the result into its own
        (unsaved) editor buffer."""
        try:
            content = await self.project_service.generate_index_yml_ai_edit(project_id, req.instruction)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"content": content}

    @post("/api/projects/{project_id}/files/index.css/ai-edit", role="admin")
    async def post_index_css_ai_edit(self, project_id: str, req: AiEditRequest):
        """The index.css (Aspect) editor's AI button — mirror of
        post_index_yml_ai_edit above, see ProjectEditor.
        generate_index_css_ai_edit. Same pure-preview contract: nothing is
        persisted, the frontend drops the result into its own (unsaved)
        editor buffer."""
        try:
            content = await self.project_service.generate_index_css_ai_edit(project_id, req.instruction)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"content": content}

    @delete("/api/projects/{project_id}/history", role="admin")
    def clear_project_history(self, project_id: str):
        """Deletes the current user's undo/redo history for every file
        in `project_id` — called when the view opens, so a fresh
        editing session never inherits a previous one's trail."""
        try:
            self.project_service.clear_project_history(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        return {"success": True}

    @put("/api/projects/{project_id}/files/{file_name:path}", role="admin")
    async def put_project_file(self, project_id: str, file_name: str, request: Request):
        """Creates or edits one of `project_id`'s files in place —
        stages a copy of the whole project dir, validates, and only on
        success replaces the real one."""
        content = await request.body()
        content_type_header = request.headers.get("content-type")
        try:
            result = await self.project_service.put_project_file(
                project_id, file_name, content, content_type_header, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return result

    @delete("/api/projects/{project_id}/files/{file_name:path}", role="admin")
    async def delete_project_file(self, project_id: str, file_name: str):
        """Deletes one text attachment from `project_id`'s directory —
        index.yml itself is rejected (see ProjectService.delete_project_file)."""
        try:
            await self.project_service.delete_project_file(project_id, file_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/projects/{project_id}/legal-terms", role="admin")
    async def post_add_legal_terms(self, project_id: str):
        """The file explorer's "New legal" action — seeds a fresh
        legal/terms.md with the platform's skeleton text server-side (see
        ProjectEditor.add_legal_terms), rather than the client crafting
        placeholder content itself."""
        try:
            return await self.project_service.add_legal_terms(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # index.yml structural editing, reusing put_project_file's own path.
    # ------------------------------------------------------------------

    @post("/api/projects/{project_id}/states", role="admin")
    async def add_state(self, project_id: str):
        try:
            return await self.project_service.add_state(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/signals", role="admin")
    async def add_signal(self, project_id: str):
        try:
            return await self.project_service.add_signal(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/env-keys", role="admin")
    async def add_env_key(self, project_id: str):
        try:
            return await self.project_service.add_env_key(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/states/{state_name}/actions", role="admin")
    async def add_action(self, project_id: str, state_name: str):
        try:
            return await self.project_service.add_action(project_id, state_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/states/{state_name}/{field}", role="admin")
    async def put_state_field(self, project_id: str, state_name: str, field: str, req: SetProjectFieldRequest):
        if field not in STATE_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable state field — expected one of {sorted(STATE_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_state_field(
                project_id, state_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/states/{state_name}/actions/{action_name}/{field}", role="admin")
    async def put_action_field(
        self, project_id: str, state_name: str, action_name: str, field: str, req: SetProjectFieldRequest
    ):
        if field not in ACTION_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable action field — expected one of {sorted(ACTION_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_action_field(
                project_id, state_name, action_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/signals/{signal_name}/{field}", role="admin")
    async def put_signal_field(self, project_id: str, signal_name: str, field: str, req: SetProjectFieldRequest):
        if field not in SIGNAL_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable signal field — expected one of {sorted(SIGNAL_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_signal_field(
                project_id, signal_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/env-keys/{env_key_name}/{field}", role="admin")
    async def put_env_key_field(self, project_id: str, env_key_name: str, field: str, req: SetProjectFieldRequest):
        if field not in ENV_KEY_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable env key field — expected one of {sorted(ENV_KEY_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_env_key_field(
                project_id, env_key_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/init-action/{field}", role="admin")
    async def put_init_action_field(self, project_id: str, field: str, req: SetProjectFieldRequest):
        """Every editable field of the init-action itself. 'target'
        (moving the automaton's start state) is the one case with its
        own validation — an unknown state name converts to 400."""
        if field not in INIT_ACTION_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable init-action field — expected one of {sorted(INIT_ACTION_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_init_action_field(
                project_id, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/project/{field}", role="admin")
    async def put_project_field(self, project_id: str, field: str, req: SetProjectFieldRequest):
        if field not in PROJECT_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable project field — expected one of {sorted(PROJECT_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_project_field(
                project_id, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    # Named to sort alphabetically before put_action_field: routes
    # register in alphabetical method-name order, and put_action_field's
    # {field} wildcard would otherwise swallow this literal "order" segment.
    @put("/api/projects/{project_id}/states/{state_name}/actions/{action_name}/order", role="admin")
    async def move_action(
        self, project_id: str, state_name: str, action_name: str, req: ReorderActionRequest
    ):
        try:
            return await self.project_service.reorder_actions(
                project_id, state_name, action_name, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/projects/{project_id}/states/{state_name}", role="admin")
    async def delete_state(self, project_id: str, state_name: str):
        try:
            await self.project_service.delete_state(project_id, state_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except InitActionTargetError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_id}/states/{state_name}/actions/{action_name}", role="admin")
    async def delete_action(self, project_id: str, state_name: str, action_name: str):
        try:
            await self.project_service.delete_action(project_id, state_name, action_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_id}/signals/{signal_name}", role="admin")
    async def delete_signal(self, project_id: str, signal_name: str):
        try:
            await self.project_service.delete_signal(project_id, signal_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_id}/env-keys/{env_key_name}", role="admin")
    async def delete_env_key(self, project_id: str, env_key_name: str):
        try:
            await self.project_service.delete_env_key(project_id, env_key_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @get("/api/projects/{project_id}/revision", role="admin")
    def get_project_revision(self, project_id: str):
        """{revision, published_revision} — the "Edit project" toolbar's
        own revision display."""
        try:
            return self.project_service.get_project_revision_info(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/publish/preview", role="admin")
    def get_publish_preview(self, project_id: str):
        """Whether a Publish right now needs an explicit state remap
        first. The Publish button's confirm flow calls this before
        POSTing, to know whether to prompt for a remap target."""
        try:
            return self.project_service.preview_publish(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/publish", role="admin")
    def post_publish_project(self, project_id: str, req: PublishProjectRequest):
        """Freezes the current draft as `project_id`'s published
        revision — see ProjectService.publish_project. `remap_to` is
        required only when get_publish_preview reported needs_remap."""
        try:
            return self.project_service.publish_project(project_id, req.remap_to)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_id}/revert", role="admin")
    async def post_revert_project(self, project_id: str):
        """Discards `project_id`'s entire in-progress draft revision,
        reverting to whatever was last published — see ProjectService.
        revert_to_published."""
        try:
            return await self.project_service.revert_to_published(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
