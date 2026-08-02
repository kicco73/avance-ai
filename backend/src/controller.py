from __future__ import annotations

import inspect
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from automaton.automaton import Automaton
from db import Db
from talk.talk_service import TalkService, TalkServiceNotAvailableError
from listen.listen_service import ListenService, ListenServiceError, ListenServiceNotAvailableError
from chat.chat_service import ChatService, ChatServiceError
from project.project_service import ProjectService
from schemas import (
    ActionRequest,
    AiModelSelectionRequest,
    AutoTrackingRequest,
    ChatMessageRequest,
    ExpectedSignalsRequest,
    ExpectedStateRequest,
    TriggersPreviewRequest,
    TruncateSessionRequest,
)


def route(method: str, path: str, **kwargs):
    def decorator(func):
        func.__route_info__ = (method, path, kwargs)
        return func
    return decorator


def get(path: str, **kwargs):
    return route("GET", path, **kwargs)


def post(path: str, **kwargs):
    return route("POST", path, **kwargs)


def put(path: str, **kwargs):
    return route("PUT", path, **kwargs)


def delete(path: str, **kwargs):
    return route("DELETE", path, **kwargs)


class AvanceController(object):
    def __init__(
        self,
        chat_service: ChatService,
        project_service: ProjectService,
        talk_service: TalkService | None,
        listen_service: ListenService | None,
        db: Db,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service
        self.db = db

        self.router = APIRouter()
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            info = getattr(member, "__route_info__", None)
            if info is not None:
                method, path, kwargs = info
                self.router.add_api_route(path, member, methods=[method], **kwargs)

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see ChatService._run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.signals.get_latest_signals()

    @get("/api/chat/metrics")
    def get_metrics(self, message_id: int | None = None):
        """Computes the metrics_framework's core metrics for the active
        user+project on demand — no caching, see metrics_framework/
        README.md. For the "Edit project" view's Inspector Metrics tab
        (no `message_id`, always the live/current history) and the
        "Label sessions" view's point-in-time Inspector (`message_id`
        restricts the history to what existed at or before that exact
        message). ChatServiceError (404 for an unknown/not-yours
        `message_id`) is handled globally, see error_handlers.py."""
        return self.chat_service.get_metrics(message_id)

    @get("/api/chat/benchmark-metrics")
    def get_benchmark_metrics(self, session_id: int | None = None):
        """Expert-annotation-vs-actual benchmark metrics (see
        metrics_framework/benchmark_metrics) for the active user+project —
        every annotated session, or (session_id given) just that one. For
        the "Label sessions" view's Performance tab. ChatServiceError
        (404 for an unknown/not-yours session_id) is handled globally,
        see error_handlers.py."""
        return self.chat_service.get_benchmark_metrics(session_id)

    @get("/api/state")
    def get_state(self):
        """Also the frontend's boot/readiness ping (see App.vue's
        pingBackend) — piggybacks talk_enabled/listen_enabled here so it
        stays the one call needed to know both "is the server up" and
        "which voice features does it actually have configured". No
        `-> StatePayload` annotation: when there's no active project/state
        yet (see the except below), the payload deliberately doesn't have
        StatePayload's fields, and FastAPI would otherwise reject that
        response as invalid instead of returning it."""

        try:
            payload = self.project_service.get_active_state_payload()
        except:
            payload = {}
            
        payload["talk_enabled"] = self.talk_service is not None
        payload["listen_enabled"] = self.listen_service is not None
        return payload

    @get("/api/ai/models")
    def get_ai_models(self):
        """The ai-service provider roster (name/model/ui_label/ui_description),
        whether auto mode is on, and which model is in effect right now
        either way — for the chat toolbar's model menu."""
        return self.chat_service.get_ai_models_info()

    @post("/api/ai/models/selection")
    def post_ai_model_selection(self, req: AiModelSelectionRequest):
        """Sets which model generate()/generate_stream() use: `index: null`
        for auto (the cascade's own fallback order), or `index` into GET
        /api/ai/models' `models` to pin one directly. Returns the same
        shape as GET /api/ai/models so the frontend can refresh in one
        round trip."""
        try:
            self.chat_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.chat_service.get_ai_models_info()

    @get("/api/chat/session")
    def get_current_session(self, session_id: int | None = None):
        """Bootstrap endpoint: resolves (or creates) the active project's
        current writable session — see chat/session_manager.py. Called by
        the frontend before it has a known session_id, or to recover from
        a stale one."""
        return self.chat_service.get_or_create_current_session(session_id)

    @post("/api/chat/sessions")
    def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return self.chat_service.create_session()

    @get("/api/chat/sessions")
    def get_sessions(self):
        """Every session for the active project, for the chat's
        "Sessions" side panel — see ChatService.list_sessions."""
        return self.chat_service.list_sessions()

    @delete("/api/chat/sessions/{session_id}")
    def delete_session(self, session_id: int):
        """Deletes a session and all its messages/signals — see
        ChatService.delete_session. Raises ChatServiceError (404) if it
        doesn't exist or belongs to someone else — handled by the global
        exception handler (see error_handlers.py), no try/except needed here."""
        self.chat_service.delete_session(session_id)
        return {"success": True}

    @post("/api/chat/sessions/{session_id}/truncate")
    async def post_truncate_session(self, session_id: int, req: TruncateSessionRequest):
        """"Restart from here" (EditProjectView.vue's chat only) — see
        ChatService.truncate_session. Same lock/response shape as
        post_reset: the live state may have just moved backward, so the
        fresh payload is read back only once the mutation (which a
        concurrent chat turn also touches) has released the lock."""
        try:
            async with self.chat_service.lock:
                self.chat_service.truncate_session(session_id, req.timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.project_service.get_active_state_payload()

    @get("/api/chat/messages")
    async def get_messages(self, session_id: int):
        return await self.chat_service.get_messages(session_id)

    @get("/api/chat/sessions/{session_id}/signals")
    def get_session_signals(self, session_id: int):
        """The full Signals event log for `session_id` (snapshots and
        transitions alike, chronological) — for the "Label sessions"
        view, which reconstructs state/signal values at any point in the
        session's timeline entirely client-side from this one call."""
        return self.chat_service.get_session_signals(session_id)

    @put("/api/chat/messages/{message_id}/expected-state")
    def put_message_expected_state(self, message_id: int, req: ExpectedStateRequest):
        """Sets or (expected_state: null) clears message_id's expert-
        annotated expected state — the "Label sessions" view's States
        tab. ChatServiceError (404 unowned/unknown message, 409 not an
        evaluation point, 422 unknown state) is handled globally, see
        error_handlers.py."""
        return self.chat_service.set_message_expected_state(message_id, req.expected_state)

    @put("/api/chat/messages/{message_id}/expected-signals")
    def put_message_expected_signals(self, message_id: int, req: ExpectedSignalsRequest):
        """Sets or clears message_id's expert-annotated expected signal
        values — the "Label sessions" view's Signals tab. Same error
        handling as put_message_expected_state, plus 422 for an unknown
        signal name or an out-of-range value."""
        return self.chat_service.set_message_expected_signals(message_id, req.expected_values)

    @delete("/api/chat/sessions/{session_id}/annotations")
    def delete_session_annotations(self, session_id: int):
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Signals rows —
        the "Label sessions" view's "Unlabel all" action, fired only
        after its own confirmation dialog. ChatServiceError (404 for an
        unknown/not-yours session_id) is handled globally, see
        error_handlers.py."""
        self.chat_service.clear_session_annotations(session_id)
        return {"success": True}

    @post("/api/chat/messages")
    async def post_message(self, req: ChatMessageRequest):
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Message cannot be empty.")
        return await self.chat_service.process_turn(text, req.session_id)

    @post("/api/action")
    async def post_action(self, req: ActionRequest):
        try:
            return await self.chat_service.apply_manual_action(req.action_name, req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/chat/autotracking")
    def get_autotracking(self):
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/chat/autotracking")
    def post_autotracking(self, req: AutoTrackingRequest):
        self.chat_service.auto_tracking_enabled = req.enabled
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @get("/api/chat/messages/{message_id}/audio")
    def get_message_audio(self, message_id: int, request: Request):
        """Generates (or replays a cached/in-flight) audio for message_id,
        streaming-compatible. 404 if the message had no [audio] tag — the
        frontend treats that as "no audio available", not a failure (see
        api.js's messageAudioUrl)."""
        if self.talk_service is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=str(TalkServiceNotAvailableError())
            )
        audio_text = self.chat_service.get_message_audio_text(message_id)
        if not audio_text:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="No audio available for this message.")
        return StreamingResponse(
            self._stream_audio_until_disconnected(request, audio_text), media_type="audio/wav"
        )

    async def _stream_audio_until_disconnected(self, request: Request, audio_text: str):
        # A dropped/aborted fetch (see audio.js's stopCurrentAudio) doesn't
        # reliably surface as a send() failure — Starlette can keep writing
        # into a closed socket for a while. Polling is_disconnected() stops
        # the provider's work immediately instead of wasting a full synthesis.
        
        if self.talk_service is None:
            raise TalkServiceNotAvailableError("Talk service is not available")
        
        generation = self.talk_service.generate(audio_text)
        try:
            async for chunk in generation:
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            aclose = getattr(generation, "aclose", None)
            if aclose and callable(aclose):
                aclose()

    @post("/api/listen/transcribe")
    async def post_listen_transcribe(self, file: UploadFile):
        """Isolated verification endpoint: not wired into process_turn or
        the chat frontend yet — just confirms ListenService end-to-end."""
        if self.listen_service is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=str(ListenServiceNotAvailableError())
            )
        audio_bytes = await file.read()
        try:
            text = await self.listen_service.transcribe(audio_bytes)
        except ListenServiceError as exc:
            raise HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"text": text}

    @post("/api/triggers/preview")
    def post_triggers_preview(self, req: TriggersPreviewRequest):
        automaton, state = self.project_service.get_active_automaton_and_state()
        names = self.chat_service.metrics.merge_if_referenced(automaton, state.key, req.signals)
        return automaton.preview_triggers(state.key, names)

    @post("/api/chat/reset")
    async def post_reset(self):
        async with self.chat_service.lock:
            self.project_service.reset_active_project()
            self.chat_service.auto_tracking_enabled = True
        return self.project_service.get_active_state_payload()

    @get("/api/backup")
    async def get_backup(self):
        """Downloads the whole working SQLite database file — every
        project, session, message, and signal, not scoped to the active
        project — as a restorable backup (see POST /api/backup)."""
        async with self.chat_service.lock:
            content = self.db.export_backup()
        filename = Path(self.db.backup_file_path()).stem + ".sqlite"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @post("/api/backup")
    async def post_backup(self, request: Request):
        """Restores the working SQLite database from an uploaded backup
        file — replaces it in place, at the exact path this server is
        configured to use (see config.database_url). Wipes whatever the
        server currently has (all projects, sessions, messages)."""
        content = await request.body()
        async with self.chat_service.lock:
            try:
                self.db.restore_backup(content)
            except ValueError as exc:
                raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
            self.chat_service.auto_tracking_enabled = True
        return {"success": True}

    @get("/api/projects")
    def get_projects(self):
        return self.project_service.list_projects()

    @put("/api/projects/{project_name}/activate")
    async def activate_project(self, project_name: str):
        try:
            await self.project_service.activate_project_idempotent(project_name, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {
            "success": True,
            "project_name": project_name,
        }

    @get("/api/projects/{project_name}")
    def get_project(self, project_name: str):
        """Downloads `project_name` as a zip — the read side of PUT
        /api/projects/{project_name}, built so it round-trips back through PUT
        with no transformation. Not restricted to the active project."""
        try:
            content = self.project_service.export_project_zip(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        encoded_project_name = quote(project_name)
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="project.zip"; filename*=UTF-8\'\'{encoded_project_name}.zip'
            },
        )

    @put("/api/projects/{project_name}")
    async def put_project(self, project_name: str, request: Request):
        """Creates or replaces `project_name` from a raw body (YAML or zip, see
        ProjectService._looks_like_zip). Stage -> validate -> only on success
        commit, swap, and wipe `project_name`'s prior conversation data."""
        content = await request.body()
        content_type = request.headers.get("content-type")

        try:
            result = await self.project_service.put_project(project_name, content, content_type, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/graph")
    def get_project_graph(self, project_name: str):
        """The project's state machine (states as nodes, actions as edges)
        of `project_name`'s last saved index.yml, for the "Edit project"
        view's Inspect panel graph — not restricted to the active project."""
        try:
            return self.project_service.get_project_graph(project_name)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/signals")
    def get_project_signals(self, project_name: str):
        """Signal definitions (name/ui_label/description) of `project_name`'s
        last saved index.yml, for the "Edit project" view's Inspect panel —
        not restricted to the active project."""
        try:
            return {"signals": self.project_service.get_project_signals(project_name)}
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
        content, for the "Edit project" view (see
        ProjectService.get_project_file) — can_undo/can_redo are what its
        Undo/Redo buttons use to know whether they're enabled."""
        try:
            return self.project_service.get_project_file(project_name, file_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects/{project_name}/files/{file_name}/undo")
    async def undo_project_file(self, project_name: str, file_name: str, request: Request):
        """Loads a step back into the current user's own undo history for
        `file_name` (see ProjectService.undo_project_file) — a pure
        editor preview: nothing is persisted, and the active project/
        conversation is never reloaded or reconciled; only Save does
        that. The request body is whatever the editor currently shows,
        needed so a later redo can bring it back."""
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
        """Deletes the current user's own undo/redo history for every
        file in `project_name` (see ProjectService.clear_project_history)
        — the "Edit project" view calls this itself when opening, so a
        fresh editing session never inherits a previous one's undo/redo
        trail."""
        try:
            self.project_service.clear_project_history(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        return {"success": True}

    @put("/api/projects/{project_name}/files/{file_name}")
    async def put_project_file(self, project_name: str, file_name: str, request: Request):
        """Creates or edits one of `project_name`'s files in place — stage a
        copy of the whole project dir, validate, and only on success replace
        the real one. Unlike PUT /api/projects/{project_name}, this never
        creates a new project."""
        content = await request.body()
        try:
            result = await self.project_service.put_project_file(project_name, file_name, content, self._activate_project)
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

    @delete("/api/projects/{project_name}")
    async def delete_project(self, project_name: str):

        try:
            await self.project_service.delete_project(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        return {"success": True}

    async def _activate_project(self, new_automaton: Automaton) -> None:
        # Unused: kept only to match ProjectService's CommitCallback shape.
        async with self.chat_service.lock:
            self.chat_service.auto_tracking_enabled = True
