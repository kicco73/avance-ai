"""The main chat window's own backend surface (ChatWindow.vue) — live
session bootstrap/messaging, env/identifiers/metrics as the Inspector
shows them there, AI model selection, talk/listen, and the handful of
cross-screen utilities (GET /api/docs/{name}, GET /api/state) that don't
belong to any one screen more than another. Split out of what used to be
one single AvanceController class in controller.py — see that module's
own docstring, and BaseController's for the shared registration
mechanism/ordering-constraint notes every *_controller.py shares.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from automaton.automaton import Automaton
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
# built from the request, so get_doc below can never be tricked into
# reading anything outside this directory (no path-traversal surface at
# all: an unknown slug is just a 404, never a filesystem lookup).
DOC_FILES = {
    "project-specs": "PROJECT_SPECS.md",
    "metrics": "METRICS.md",
    "benchmark": "BENCHMARK.md",
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
        reference docs (see DOC_FILES) — backs each "(?)" documentation
        button (EditProjectView.vue's own, next to Save; the Inspector's
        Metrics/Performance tabs) with the actual .md file's content
        instead of duplicating it into the frontend bundle. `name` not in
        DOC_FILES is a 404, not a filesystem error."""
        filename = DOC_FILES.get(name)
        if filename is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"Unknown doc '{name}'.")
        return {"content": (DOCS_DIR / filename).read_text()}

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see TrackingService.run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.tracking_service.get_latest_signals()

    @get("/api/chat/env")
    def get_env(self, message_id: int | None = None):
        """{"stored": ..., "action_set": ...} — the active user+project's
        current "environment" memory (see tracking.env.Env), split so the
        "Edit project" view's Inspector Env tab knows which section each
        value belongs in ("AI"/"ACTION") and which are actually
        editable/deletable (only the stored — "AI" — ones, see PUT/DELETE
        below; "ACTION" values are only ever cleared as a whole, see
        DELETE /api/chat/action-env). No "computed" section anymore —
        system/session/metric facts (see tracking.evaluation_scope.
        EvaluationScopeBuilder) are evaluation-scope-only now, never
        rendered in the Inspector (see GET /api/chat/identifiers instead,
        for what's actually referenceable). Live/current, or
        (`message_id` given) as of that exact message — same
        point-in-time convention as GET /api/chat/metrics. ChatServiceError
        (404 for an unknown/not-yours `message_id`) is handled globally,
        see error_handlers.py."""
        return self.chat_service.get_env(message_id)

    @delete("/api/chat/env")
    def clear_env(self):
        """Wipes every stored env key at once (see ChatService.clear_env)
        — the Inspector Env tab's own "clear all" button for the AI
        section. Always live."""
        return self.chat_service.clear_env()

    # A distinct top-level path, not /api/chat/env/action — this
    # controller's own register_routes (see BaseController) registers
    # routes in inspect.getmembers' own alphabetical-by-method-name
    # order, not source order, so a path under /api/chat/env/{key} could
    # easily end up registered before a literal /api/chat/env/action and
    # swallow it as key="action". A completely different path sidesteps
    # that footgun outright rather than relying on naming this method to
    # sort correctly.
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

    @get("/api/chat/identifiers")
    def get_identifiers(self):
        """The active project's own identifier registry (see automaton.
        identifier_registry.build_registry/ProjectService.
        get_active_identifier_registry) — every identifier a trigger/
        `env:` expression can reference, one {identifier: description}
        dict per namespace (signal, env, system, session, session.metric,
        metric)."""
        try:
            return self.project_service.get_active_identifier_registry()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc

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
        a stale one. Always a real, published-revision session — see
        GET /api/projects/{project_name}/test-sessions/current for
        EditProjectView.vue's own embedded "Test" chat, the only caller
        allowed a draft one instead. No `allow_draft` parameter here or on
        POST /api/chat/sessions below anymore: which revision a session
        may exist against is decided solely by which endpoint is called,
        never by a caller-supplied flag on a shared one."""
        return self.chat_service.get_or_create_current_session(session_id)

    @post("/api/chat/sessions")
    def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return self.chat_service.create_session()

    @get("/api/chat/sessions")
    def get_sessions(self, include_imported: bool = False):
        """Every session for the active project, for the chat's
        "Sessions" side panel — see ChatService.list_sessions."""
        return self.chat_service.list_sessions(include_imported=include_imported)

    @get("/api/chat/messages")
    async def get_messages(self, session_id: int):
        return await self.chat_service.get_messages(session_id)

    @post("/api/chat/messages")
    async def post_message(self, req: ChatMessageRequest):
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Message cannot be empty.")
        return await self.chat_service.process_turn(req.session_id, text)

    @post("/api/action")
    async def post_action(self, req: ActionRequest):
        try:
            return await self.chat_service.apply_manual_action(req.action_name, req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/chat/autotracking")
    def get_autotracking(self):
        return {"enabled": self.chat_service.tracking_service.auto_tracking_enabled}

    @post("/api/chat/autotracking")
    def post_autotracking(self, req: AutoTrackingRequest):
        self.chat_service.tracking_service.auto_tracking_enabled = req.enabled
        return {"enabled": self.chat_service.tracking_service.auto_tracking_enabled}

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
        scope = self.chat_service.evaluation_scope_builder.build(automaton, state.key, req.signals)
        return automaton.preview_triggers(state.key, scope)

    @post("/api/chat/reset")
    async def post_reset(self):
        """Wipes this user's own sessions for the active project (see
        ProjectService.reset_active_project) — the next state resolution
        falls all the way back to init_action.target (see _resolve_state's
        own docstring on that fallback), same as a session's very first
        transition ever, so on-enter rides along here the same way it
        does on every other real transition (see ChatService.
        apply_manual_action/process_turn's own "on-enter") — this is
        exactly one, just not modeled as firing an Action per se."""
        async with self.chat_service.lock:
            self.project_service.reset_active_project()
            self.chat_service.tracking_service.auto_tracking_enabled = True
        automaton, state = self.project_service.get_active_automaton_and_state()
        return {**Automaton.get_state_payload(state), "on-enter": automaton.init_action.on_enter}
