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
from chat.sse_turn import SseChatTurn
from listen.listen_service import ListenService, ListenServiceError, ListenServiceNotAvailableError
from project.project_service import ProjectService
from talk.talk_service import TalkService, TalkServiceNotAvailableError
from schemas import (
    ActionRequest,
    ActuatorsRequest,
    AiModelSelectionRequest,
    AutoTrackingRequest,
    ChatMessageRequest,
    ReactionRequest,
    SetEnvValueRequest,
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
    "session-specs": "SESSION_SPECS.md",
    "skin-specs": "SKIN_SPECS.md",
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
        return {"content": (DOCS_DIR / filename).read_text(encoding="utf-8")}

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see TrackingService.run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.get_latest_signals()

    @get("/api/chat/sessions/{session_id}/env")
    def get_env(self, session_id: int, message_id: int | None = None):
        """{"stored": ..., "action_set": ...} — session_id's own
        "environment" memory, split so the Inspector Env tab knows which
        section each value belongs in (only "stored" is editable)."""
        return self.chat_service.get_env(session_id, message_id)

    @delete("/api/chat/sessions/{session_id}/env")
    def clear_env(self, session_id: int):
        """Wipes every stored and action-set env key at once for
        session_id (see ChatService.clear_env)."""
        return self.chat_service.clear_env(session_id)

    @put("/api/chat/sessions/{session_id}/env/{key}")
    def put_env_value(self, session_id: int, key: str, req: SetEnvValueRequest):
        """Edits one stored env key (see ChatService.set_env_value) —
        the Inspector Env tab's own "click a value to edit it". Always
        current: there's no "editing history"."""
        try:
            return self.chat_service.set_env_value(session_id, key, req.value)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @delete("/api/chat/sessions/{session_id}/env/{key}")
    def delete_env_value(self, session_id: int, key: str):
        """Removes one stored env key outright (see ChatService.
        delete_env_key) — the Inspector Env tab's own delete button."""
        try:
            return self.chat_service.delete_env_key(session_id, key)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/identifiers")
    def get_identifiers(self, project_id: str):
        """`project_id`'s own identifier registry — every identifier a
        trigger/`env:` expression can reference, one {identifier:
        description} dict per namespace."""
        try:
            return self.project_service.get_identifier_registry(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/metrics")
    def get_metrics(self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None):
        """Core metrics for `project_id`, live or (`message_id` given)
        as of that exact message — no caching. `full`: every core metric,
        including ones that need more than one session (e.g. Retention),
        instead of the usual "one_session" subset. `username` (omitted:
        the caller's own sessions): Manage Users' statistics panel, to
        inspect a specific user's sessions rather than its own. ChatServiceError
        for an unknown message_id is handled globally, see error_handlers.py."""
        return self.chat_service.get_metrics(
            project_id=project_id, message_id=message_id, full=full, username=username,
        )

    @get("/api/projects/{project_id}/users/{username}/latest-signals")
    def get_user_latest_signals(self, project_id: str, username: str):
        """The most recent live session's own latest signal snapshot for
        `username` in `project_id` — Manage Users' Signals tab."""
        return self.chat_service.get_latest_signal_values(project_id, username)

    @get("/api/projects/{project_id}/users/{username}/timeline")
    def get_user_timeline(self, project_id: str, username: str):
        return self.chat_service.get_timeline(project_id, username)

    @get("/api/projects/{project_id}/users/{username}/metrics-history")
    def get_user_metrics_history(self, project_id: str, username: str):
        return self.chat_service.get_metrics_history(project_id, username)

    @get("/api/state")
    def get_state(self):
        """Also the frontend's boot/readiness ping — piggybacks
        talk_enabled/listen_enabled here. No `-> StatePayload` annotation:
        with no active project/state the payload lacks those fields.
        talk_enabled here is the AND of two independent things: whether
        the server has any TTS provider configured at all (talk_service),
        and whether the active project itself opted in (its own
        project.talk-enabled, defaulting true) — the chat toolbar's
        audio/spoken-text icons read this one combined flag rather than
        checking the project's own setting separately."""

        try:
            payload = self.project_service.get_active_state_payload()
        except:
            payload = {}

        try:
            project_talk_enabled = self.project_service.get_active_automaton().talk_enabled
        except:
            project_talk_enabled = True

        payload["talk_enabled"] = self.talk_service is not None and project_talk_enabled
        payload["listen_enabled"] = self.listen_service is not None and self.listen_service.enabled
        payload["input_token_budget_per_turn"] = self.chat_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.chat_service.get_total_token_budget_per_session()
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

    @get("/api/ai/models/test")
    def get_ai_test_models(self):
        return self.chat_service.get_test_ai_models_info()

    @post("/api/ai/models/test/selection")
    def post_ai_test_model_selection(self, req: AiModelSelectionRequest):
        try:
            self.chat_service.select_test_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.chat_service.get_test_ai_models_info()
        return self.chat_service.get_ai_models_info()

    @get("/api/chat/session")
    async def get_current_session(self, session_id: int | None = None):
        """Bootstrap endpoint: resolves (or creates) the active project's
        current writable session. Always a real, published-revision
        session — see the test-sessions/current endpoint for the draft equivalent."""
        return await self.chat_service.get_current_session_if_any_or_create_new(session_id)

    @post("/api/chat/sessions")
    async def post_create_session(self):
        """Explicit "start a new session" action — always creates one,
        superseding whichever session was previously current."""
        return await self.chat_service.create_session()

    @get("/api/projects/{project_id}/legal-terms-status")
    def get_legal_terms_status(self, project_id: str):
        return self.chat_service.get_legal_terms_status(project_id)

    @post("/api/projects/{project_id}/accept-terms")
    def post_accept_chat_terms(self, project_id: str):
        self.chat_service.accept_legal_terms(project_id)
        return {"success": True}

    @get("/api/projects/{project_id}/sessions")
    def get_sessions(self, project_id: str, include_imported: bool = False):
        """Every session for `project_id`, for the "Sessions" side
        panel — see ChatService.list_sessions."""
        return self.chat_service.list_sessions(include_imported=include_imported, project_id=project_id)

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
        return SseChatTurn(self.chat_service, session_id, text).response()

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

    @get("/api/chat/sessions/{session_id}/actuators")
    def get_actuators(self, session_id: int):
        return {"enabled": self.chat_service.is_actuators_enabled(session_id)}

    @post("/api/chat/sessions/{session_id}/actuators")
    def post_actuators(self, session_id: int, req: ActuatorsRequest):
        self.chat_service.set_actuators_enabled(session_id, req.enabled)
        return {"enabled": self.chat_service.is_actuators_enabled(session_id)}

    @put("/api/chat/messages/{message_id}/reaction")
    def put_message_reaction(self, message_id: int, req: ReactionRequest):
        """Sets or (reaction: null) clears the user's own reaction to
        message_id — a bot message, chosen from the active project's
        `reactions` dict. ChatServiceError (404) is handled globally."""
        return self.chat_service.set_message_reaction(message_id, req.reaction)

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

