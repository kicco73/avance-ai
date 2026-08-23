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
from schemas import PublishProjectRequest, ReorderActionRequest, SetProjectFieldRequest

from .base_controller import BaseController, delete, get, post, put
from .project_commit_mixin import ProjectCommitMixin

# Explicit per-type whitelists for the field-by-field edit endpoints
# below — name/key is deliberately never in any of these three: it's
# generated once at creation and immutable from then on.
STATE_EDITABLE_FIELDS = {"ui-label", "ui-description", "history-cutoff", "contextual-prompt", "chat", "reactions-enabled"}
ACTION_EDITABLE_FIELDS = {"ui-label", "ui-description", "action-prompt", "target", "trigger", "on-enter"}
SIGNAL_EDITABLE_FIELDS = {"ui-label", "ui-description", "definition"}
# Unlike a state/action/signal, an env key has no separate ui-label to
# derive its name from — 'name' is itself directly editable here.
ENV_KEY_EDITABLE_FIELDS = {"name", "ui-description", "value"}
# The optional top-level `project:` section — 'id' is what other
# projects reach this one as through automaton.<id>.
PROJECT_EDITABLE_FIELDS = {"id", "ui-label", "ui-description", "talk-enabled", "signal-tracking-on-ai-message"}


class EditProjectController(BaseController, ProjectCommitMixin):

    def __init__(self, chat_service: ChatService, project_service: ProjectService) -> None:
        self.chat_service = chat_service
        self.project_service = project_service

    @post("/api/projects/{project_name}/test-sessions")
    def post_create_test_session(self, project_name: str):
        """The embedded "Test" chat's explicit "start a new session"
        action — the one place a session may exist against an unpublished
        revision."""
        return self.chat_service.create_draft_session(project_name)

    @get("/api/projects/{project_name}/test-sessions/current")
    def get_current_test_session(self, project_name: str, session_id: int | None = None):
        """The embedded "Test" chat's bootstrap endpoint — the
        draft-session equivalent of GET /api/chat/session."""
        return self.chat_service.get_or_create_current_draft_session(session_id, project_name)

    @get("/api/projects/{project_name}/test-sessions")
    def get_test_sessions(self, project_name: str):
        """The embedded "Test" chat's own "Sessions" panel listing — the
        draft-session equivalent of GET .../sessions. The two pools never mix."""
        return self.chat_service.list_test_sessions(project_name)

    @post("/api/projects/{project_name}/test-sessions/reset")
    async def post_reset_test_sessions(self, project_name: str):
        async with self.chat_service.acquire_write(project_name):
            return self.chat_service.reset_test_sessions(project_name)

    @get("/api/projects/{project_name}/states")
    def get_project_states(self, project_name: str):
        """Every real state key of `project_name`'s current draft
        automaton — the "States" branch's own node list (see
        TestsTree.vue)."""
        try:
            return self.project_service.get_project_states(project_name)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/graph")
    def get_project_graph(self, project_name: str, session_id: int | None = None):
        """The project's state machine (states as nodes, actions as
        edges), for the Inspect panel graph. `session_id` omitted
        resolves the current draft; given, resolves that session's revision."""
        try:
            return self.project_service.get_project_graph(project_name, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/signals")
    def get_project_signals(self, project_name: str, state_key: str | None = None, session_id: int | None = None):
        """Signal definitions for the Inspect panel. `state_key`, when
        given, scopes each signal's `relevant` field to that state's
        outgoing actions. `session_id`: see get_project_graph above."""
        try:
            return {"signals": self.project_service.get_project_signals(project_name, state_key, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/env-keys")
    def get_project_env_keys(self, project_name: str, session_id: int | None = None):
        """Declared env-key definitions for the "Edit project" view's
        Inspect panel Env tab. `session_id`: see get_project_graph above."""
        try:
            return {"env_keys": self.project_service.get_project_env_keys(project_name, session_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/project")
    def get_project_metadata(self, project_name: str):
        """The optional top-level `project:` section of `project_name`'s
        last saved index.yml, for the Inspect panel Info tab."""
        try:
            return {"project": self.project_service.get_project_metadata(project_name)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/files")
    def get_project_files(self, project_name: str):
        """Text-editable files inside `project_name`'s directory (index.yml
        plus any text attachments), for the "Edit project" view's file
        explorer panel."""
        try:
            return {"files": self.project_service.list_project_files(project_name)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/files/{file_name}")
    def get_project_file(self, project_name: str, file_name: str):
        """{content, can_undo, can_redo} of `file_name`'s current
        content — can_undo/can_redo drive the Undo/Redo buttons. Missing
        index.css reports 204 instead of 404, since it's optional."""
        try:
            return self.project_service.get_project_file(project_name, file_name)
        except FileNotFoundError as exc:
            if file_name == "index.css":
                return Response(status_code=HTTPStatus.NO_CONTENT)
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/files/{file_name}/content")
    def get_project_file_content(self, project_name: str, file_name: str, request: Request, session_id: int | None = None):
        """Raw bytes of `file_name`'s content, for callers that can't use
        the JSON GET above. ETag'd off the content itself, so an
        unchanged file 304s on a matching If-None-Match."""
        try:
            content, content_type = self.project_service.get_project_file_content(project_name, file_name, session_id)
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

    @post("/api/projects/{project_name}/files/{file_name}/undo")
    async def undo_project_file(self, project_name: str, file_name: str, request: Request):
        """Loads a step back into the current user's undo history for
        `file_name` — a pure editor preview: nothing is persisted, and
        the active project is never reloaded; only Save does that."""
        content = await request.body()
        try:
            return await self.project_service.undo_project_file(project_name, file_name, content)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/files/{file_name}/redo")
    async def redo_project_file(self, project_name: str, file_name: str, request: Request):
        """Mirror of .../undo, replaying the current user's own redo
        history instead (see ProjectService.redo_project_file)."""
        content = await request.body()
        try:
            return await self.project_service.redo_project_file(project_name, file_name, content)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/projects/{project_name}/history")
    def clear_project_history(self, project_name: str):
        """Deletes the current user's undo/redo history for every file
        in `project_name` — called when the view opens, so a fresh
        editing session never inherits a previous one's trail."""
        try:
            self.project_service.clear_project_history(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        return {"success": True}

    @put("/api/projects/{project_name}/files/{file_name}")
    async def put_project_file(self, project_name: str, file_name: str, request: Request):
        """Creates or edits one of `project_name`'s files in place —
        stages a copy of the whole project dir, validates, and only on
        success replaces the real one."""
        content = await request.body()
        content_type_header = request.headers.get("content-type")
        try:
            result = await self.project_service.put_project_file(
                project_name, file_name, content, content_type_header, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return result

    @delete("/api/projects/{project_name}/files/{file_name}")
    async def delete_project_file(self, project_name: str, file_name: str):
        """Deletes one text attachment from `project_name`'s directory —
        index.yml itself is rejected (see ProjectService.delete_project_file)."""
        try:
            await self.project_service.delete_project_file(project_name, file_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    # ------------------------------------------------------------------
    # index.yml structural editing, reusing put_project_file's own path.
    # ------------------------------------------------------------------

    @post("/api/projects/{project_name}/states")
    async def add_state(self, project_name: str):
        try:
            return await self.project_service.add_state(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/signals")
    async def add_signal(self, project_name: str):
        try:
            return await self.project_service.add_signal(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/env-keys")
    async def add_env_key(self, project_name: str):
        try:
            return await self.project_service.add_env_key(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/states/{state_name}/actions")
    async def add_action(self, project_name: str, state_name: str):
        try:
            return await self.project_service.add_action(project_name, state_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/states/{state_name}/{field}")
    async def put_state_field(self, project_name: str, state_name: str, field: str, req: SetProjectFieldRequest):
        if field not in STATE_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable state field — expected one of {sorted(STATE_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_state_field(
                project_name, state_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/states/{state_name}/actions/{action_name}/{field}")
    async def put_action_field(
        self, project_name: str, state_name: str, action_name: str, field: str, req: SetProjectFieldRequest
    ):
        if field not in ACTION_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable action field — expected one of {sorted(ACTION_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_action_field(
                project_name, state_name, action_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/signals/{signal_name}/{field}")
    async def put_signal_field(self, project_name: str, signal_name: str, field: str, req: SetProjectFieldRequest):
        if field not in SIGNAL_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable signal field — expected one of {sorted(SIGNAL_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_signal_field(
                project_name, signal_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/env-keys/{env_key_name}/{field}")
    async def put_env_key_field(self, project_name: str, env_key_name: str, field: str, req: SetProjectFieldRequest):
        if field not in ENV_KEY_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable env key field — expected one of {sorted(ENV_KEY_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_env_key_field(
                project_name, env_key_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/init-action/{field}")
    async def put_init_action_field(self, project_name: str, field: str, req: SetProjectFieldRequest):
        """Every editable field of the init-action itself. 'target'
        (moving the automaton's start state) is the one case with its
        own validation — an unknown state name converts to 400."""
        try:
            return await self.project_service.set_init_action_field(
                project_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/project/{field}")
    async def put_project_field(self, project_name: str, field: str, req: SetProjectFieldRequest):
        if field not in PROJECT_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"'{field}' is not an editable project field — expected one of {sorted(PROJECT_EDITABLE_FIELDS)}.",
            )
        try:
            return await self.project_service.set_project_field(
                project_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    # Named to sort alphabetically before put_action_field: routes
    # register in alphabetical method-name order, and put_action_field's
    # {field} wildcard would otherwise swallow this literal "order" segment.
    @put("/api/projects/{project_name}/states/{state_name}/actions/{action_name}/order")
    async def move_action(
        self, project_name: str, state_name: str, action_name: str, req: ReorderActionRequest
    ):
        try:
            return await self.project_service.reorder_actions(
                project_name, state_name, action_name, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/projects/{project_name}/states/{state_name}")
    async def delete_state(self, project_name: str, state_name: str):
        try:
            await self.project_service.delete_state(project_name, state_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except InitActionTargetError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_name}/states/{state_name}/actions/{action_name}")
    async def delete_action(self, project_name: str, state_name: str, action_name: str):
        try:
            await self.project_service.delete_action(project_name, state_name, action_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_name}/signals/{signal_name}")
    async def delete_signal(self, project_name: str, signal_name: str):
        try:
            await self.project_service.delete_signal(project_name, signal_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @delete("/api/projects/{project_name}/env-keys/{env_key_name}")
    async def delete_env_key(self, project_name: str, env_key_name: str):
        try:
            await self.project_service.delete_env_key(project_name, env_key_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @get("/api/projects/{project_name}/revision")
    def get_project_revision(self, project_name: str):
        """{revision, published_revision} — the "Edit project" toolbar's
        own revision display."""
        try:
            return self.project_service.get_project_revision_info(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/publish/preview")
    def get_publish_preview(self, project_name: str):
        """Whether a Publish right now needs an explicit state remap
        first. The Publish button's confirm flow calls this before
        POSTing, to know whether to prompt for a remap target."""
        try:
            return self.project_service.preview_publish(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/publish")
    def post_publish_project(self, project_name: str, req: PublishProjectRequest):
        """Freezes the current draft as `project_name`'s published
        revision — see ProjectService.publish_project. `remap_to` is
        required only when get_publish_preview reported needs_remap."""
        try:
            return self.project_service.publish_project(project_name, req.remap_to)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/revert")
    async def post_revert_project(self, project_name: str):
        """Discards `project_name`'s entire in-progress draft revision,
        reverting to whatever was last published — see ProjectService.
        revert_to_published."""
        try:
            return await self.project_service.revert_to_published(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
