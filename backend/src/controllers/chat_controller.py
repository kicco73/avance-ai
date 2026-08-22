"""The main chat window's own backend surface (ChatWindow.vue) — live
session bootstrap/messaging, env/identifiers/metrics as the Inspector
shows them there, AI model selection, talk/listen, and the handful of
cross-screen utilities (GET /api/docs/{name}, GET /api/state) that don't
belong to any one screen more than another.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from chat.chat_service import ChatService
from listen.listen_service import ListenService, ListenServiceError, ListenServiceNotAvailableError
from project.project_service import ProjectService
from talk.talk_service import TalkService, TalkServiceNotAvailableError
from schemas import (
    ActionRequest,
    AiModelSelectionRequest,
    AutoTrackingRequest,
    ChatMessageRequest,
    SetEnvValueRequest,
    TriggersPreviewRequest,
)

from .base_controller import BaseController, delete, get, post, put

# Slug -> filename under src/docs/ — a fixed allow-list, not a raw path
# built from the request, so get_doc can never be tricked into reading
# anything outside this directory.
DOC_FILES = {
    "project-specs": "PROJECT_SPECS.md",
    "metrics": "METRICS.md",
    "benchmark": "BENCHMARK.md",
    "markdown-guide": "MARKDOWN_GUIDE.md",
}
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


class ChatController(BaseController):

    def __init__(
        self,
        chat_service: ChatService,
        project_service: ProjectService,
        talk_service: TalkService | None,
        listen_service: ListenService | None,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service

    @get("/api/docs/{name}")
    def get_doc(self, name: str):
        """Raw markdown content of one of src/docs/'s fixed set of
        reference docs — backs each "(?)" documentation button instead
        of duplicating it into the frontend bundle. Unknown `name` is a 404."""
        filename = DOC_FILES.get(name)
        if filename is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"Unknown doc '{name}'.")
        return {"content": (DOCS_DIR / filename).read_text()}

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see TrackingService.run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.get_latest_signals()

    @get("/api/chat/env")
    def get_env(self, message_id: int | None = None):
        """{"stored": ..., "action_set": ...} — the active project's
        "environment" memory, split so the Inspector Env tab knows which
        section each value belongs in (only "stored" is editable)."""
        return self.chat_service.get_env(message_id)

    @delete("/api/chat/env")
    def clear_env(self):
        """Wipes every stored env key at once (see ChatService.clear_env)
        — the Inspector Env tab's own "clear all" button for the AI
        section. Always live."""
        return self.chat_service.clear_env()

    # A distinct top-level path, not /api/chat/env/action — routes
    # register alphabetically by method name, so /api/chat/env/{key}
    # could end up registered first and swallow it as key="action".
    @delete("/api/chat/action-env")
    def clear_action_env(self):
        """Wipes every action-set env key at once (see ChatService.
        clear_action_env) — the Inspector Env tab's own "clear all"
        button for the ACTION section. Always live."""
        return self.chat_service.clear_action_env()

    @put("/api/chat/env/{key}")
    def put_env_value(self, key: str, req: SetEnvValueRequest):
        """Edits one stored env key (see ChatService.set_env_value) —
        the Inspector Env tab's own "click a value to edit it". Always
        live: there's no "editing history"."""
        try:
            return self.chat_service.set_env_value(key, req.value)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/chat/env/{key}")
    def delete_env_value(self, key: str):
        """Removes one stored env key outright (see ChatService.
        delete_env_key) — the Inspector Env tab's own delete button."""
        try:
            return self.chat_service.delete_env_key(key)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/identifiers")
    def get_identifiers(self, project_name: str):
        """`project_name`'s own identifier registry — every identifier a
        trigger/`env:` expression can reference, one {identifier:
        description} dict per namespace."""
        try:
            return self.project_service.get_identifier_registry(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/metrics")
    def get_metrics(self, project_name: str, message_id: int | None = None, full: bool = False, username: str | None = None):
        """Core metrics for `project_name`, live or (`message_id` given)
        as of that exact message — no caching. `full`: every core metric,
        including ones that need more than one session (e.g. Retention),
        instead of the usual "one_session" subset. `username` (omitted:
        the caller's own sessions): Manage Users' statistics panel, to
        inspect a specific user's sessions rather than its own. ChatServiceError
        for an unknown message_id is handled globally, see error_handlers.py."""
        return self.chat_service.get_metrics(
            project_name=project_name, message_id=message_id, full=full, username=username,
        )

    @get("/api/state")
    def get_state(self):
        """Also the frontend's boot/readiness ping — piggybacks
        talk_enabled/listen_enabled here. No `-> StatePayload` annotation:
        with no active project/state the payload lacks those fields."""

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
        """Sets which model generate()/generate_stream() use: `index:
        null` for auto (the cascade's fallback order), or `index` into
        GET /api/ai/models' `models` to pin one directly."""
        try:
            self.chat_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.chat_service.get_ai_models_info()

    @get("/api/chat/session")
    def get_current_session(self, session_id: int | None = None):
        """Bootstrap endpoint: resolves (or creates) the active project's
        current writable session. Always a real, published-revision
        session — see the test-sessions/current endpoint for the draft equivalent."""
        return self.chat_service.get_or_create_current_session(session_id)

    @post("/api/chat/sessions")
    def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return self.chat_service.create_session()

    @get("/api/projects/{project_name}/sessions")
    def get_sessions(self, project_name: str, include_imported: bool = False):
        """Every session for `project_name`, for the "Sessions" side
        panel — see ChatService.list_sessions."""
        return self.chat_service.list_sessions(include_imported=include_imported, project_name=project_name)

    @get("/api/chat/sessions/{session_id}/state")
    def get_session_state(self, session_id: int):
        return self.chat_service.get_state_for_session(session_id)

    @get("/api/chat/sessions/{session_id}/messages")
    async def get_messages(self, session_id: int):
        return await self.chat_service.get_messages(session_id)

    @post("/api/chat/sessions/{session_id}/messages")
    async def post_message(self, session_id: int, req: ChatMessageRequest):
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Message cannot be empty.")
        return await self.chat_service.process_turn(session_id, text)

    @post("/api/chat/sessions/{session_id}/action")
    async def post_action(self, session_id: int, req: ActionRequest):
        try:
            return await self.chat_service.apply_manual_action(req.action_name, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/chat/sessions/{session_id}/autotracking")
    def get_autotracking(self, session_id: int):
        """"Dev mode: freeze automatic state transitions" — EditProjectView.
        vue's own embedded "Test" chat only; a native/imported session is
        always auto-tracked (see TrackingService.process)."""
        return {"enabled": self.chat_service.is_auto_tracking_enabled(session_id)}

    @post("/api/chat/sessions/{session_id}/autotracking")
    def post_autotracking(self, session_id: int, req: AutoTrackingRequest):
        self.chat_service.set_auto_tracking_enabled(session_id, req.enabled)
        return {"enabled": self.chat_service.is_auto_tracking_enabled(session_id)}

    @get("/api/chat/messages/{message_id}/audio")
    def get_message_audio(self, message_id: int, request: Request):
        """Generates (or replays a cached/in-flight) audio for message_id,
        streaming-compatible. 404 if the message had no [audio] tag — the
        frontend treats that as "no audio available", not a failure."""
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
        # A dropped/aborted fetch doesn't reliably surface as a send()
        # failure — polling is_disconnected() stops the provider's work
        # immediately instead of wasting a full synthesis.
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
        return self.chat_service.preview_triggers(req.signals)

