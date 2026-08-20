from __future__ import annotations

import hashlib
import inspect
import json
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from automaton.automaton import Automaton
from automaton.automaton_yaml_editor import InitActionTargetError
from db import Db
from talk.talk_service import TalkService, TalkServiceNotAvailableError
from listen.listen_service import ListenService, ListenServiceError, ListenServiceNotAvailableError
from chat.chat_service import ChatService, ChatServiceError
from metrics.benchmark_run_service import BenchmarkRunService
from project.project_service import ProjectService
from session import Session
from tracking.tracking_service import TrackingService
from schemas import (
    ActionRequest,
    AiModelSelectionRequest,
    AutoTrackingRequest,
    ChatMessageRequest,
    CommentRequest,
    CreateBenchmarkRunRequest,
    ExpectedSignalsRequest,
    ExpectedStateRequest,
    PublishProjectRequest,
    ReorderActionRequest,
    SessionImportJsonRequest,
    SetEnvValueRequest,
    SetProjectFieldRequest,
    SetSessionLabeledRequest,
    SetSessionTitleRequest,
    StateTestRequest,
    TriggersPreviewRequest,
    TruncateSessionRequest,
)


# Slug -> filename under src/docs/ — a fixed allow-list, not a raw path
# built from the request, so get_doc below can never be tricked into
# reading anything outside this directory (no path-traversal surface at
# all: an unknown slug is just a 404, never a filesystem lookup).
DOC_FILES = {
    "project-specs": "PROJECT_SPECS.md",
    "metrics": "METRICS.md",
    "benchmark": "BENCHMARK.md",
}
DOCS_DIR = Path(__file__).resolve().parent / "docs"

# Explicit per-type whitelists for the field-by-field state/action/signal
# edit endpoints (see put_state_field/put_action_field/put_signal_field
# below) — name/key is deliberately never in any of these three: it's
# generated once at creation (see AutomatonYamlEditor.add_state/
# add_action) and immutable from then on, so there's no edit endpoint
# for it at all, for a state or an action. Only a signal's own `name` can
# ever change, and only as a side effect of editing its own `ui-label`
# (see AutomatonYamlEditor.set_signal_field), never through a field edit
# of its own.
STATE_EDITABLE_FIELDS = {"ui-label", "ui-description", "history-cutoff", "contextual-prompt", "chat"}
ACTION_EDITABLE_FIELDS = {"ui-label", "ui-description", "action-prompt", "target", "trigger"}
SIGNAL_EDITABLE_FIELDS = {"ui-label", "ui-description", "definition"}
# Unlike a state/action/signal's own name/ui-label, an env key has no
# separate ui-label to derive its name from — 'name' is itself directly
# editable here, sanitized through the same to_snake_case rename path
# (see AutomatonYamlEditor.set_env_key_field/rename_env_key).
ENV_KEY_EDITABLE_FIELDS = {"name", "ui-description", "value"}


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
        tracking_service: TrackingService,
        benchmark_run_service: BenchmarkRunService,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.talk_service = talk_service
        self.listen_service = listen_service
        self.db = db
        self.benchmark_run_service = benchmark_run_service
        self.tracking_service = tracking_service

        self.router = APIRouter()
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            info = getattr(member, "__route_info__", None)
            if info is not None:
                method, path, kwargs = info
                self.router.add_api_route(path, member, methods=[method], **kwargs)

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

    # A distinct top-level path, not /api/chat/env/action — this router
    # (see __init__ above) registers routes in inspect.getmembers' own
    # alphabetical-by-method-name order, not source order, so a path
    # under /api/chat/env/{key} could easily end up registered before a
    # literal /api/chat/env/action and swallow it as key="action". A
    # completely different path sidesteps that footgun outright rather
    # than relying on naming this method to sort correctly.
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

    @post("/api/projects/{project_name}/test-sessions")
    def post_create_test_session(self, project_name: str):
        """EditProjectView.vue's own embedded "Test" chat, its own
        explicit "start a new session" action — the one place a session
        may exist against a revision nobody's published yet (see
        db.create_draft_chat_session's own docstring). `project_name`
        isn't itself passed through to ChatService (see ChatService.
        create_draft_session, which — same as every other ChatService
        method — always operates on whichever project the active user's
        session already has activated, see PUT /api/projects/
        {project_name}/activate): it's here so this endpoint reads, in the
        URL alone, as unambiguously project-scoped and draft-only, the
        same convention as every other /api/projects/{project_name}/...
        route."""
        return self.chat_service.create_draft_session()

    @get("/api/projects/{project_name}/test-sessions/current")
    def get_current_test_session(self, project_name: str, session_id: int | None = None):
        """EditProjectView.vue's own embedded "Test" chat, its own bootstrap
        endpoint — the draft-session equivalent of GET /api/chat/session
        above (see that one's own docstring, and post_create_test_session's
        own on why `project_name` is here but unused beyond the URL)."""
        return self.chat_service.get_or_create_current_draft_session(session_id)

    @get("/api/projects/{project_name}/test-sessions")
    def get_test_sessions(self, project_name: str):
        """EditProjectView.vue's own embedded "Test" chat, its own
        "Sessions" panel listing (see ChatService.list_test_sessions) —
        the draft-session equivalent of GET /api/chat/sessions. Never
        shows (and GET /api/chat/sessions never shows) the other pool's
        own sessions — the two are fully isolated, not just filtered
        views over one shared list."""
        return self.chat_service.list_test_sessions()

    @get("/api/chat/sessions")
    def get_sessions(self, include_imported: bool = False):
        """Every session for the active project, for the chat's
        "Sessions" side panel — see ChatService.list_sessions."""
        return self.chat_service.list_sessions(include_imported=include_imported)

    @post("/api/chat/sessions/import")
    async def post_import_session(self, file: UploadFile):
        """Imports a chat session from a plain-text transcript (see
        TrackingService.import_session/tracking.session_import.
        parse_transcript) — annotatable/testable without ever having run
        through a live conversation. Uses the active project, same
        convention as POST /api/chat/sessions. No try/except: a malformed
        transcript raises TrackingServiceError, already handled by the
        global exception handler (see error_handlers.py)."""
        content = await file.read()
        text = content.decode("utf-8")
        session_id = self.tracking_service.import_session(
            Session().user, self.project_service.get_active_project_name(), text, title=file.filename
        )
        return {"success": True, "session_id": session_id}

    @post("/api/chat/sessions/import-json")
    def post_import_session_json(self, req: SessionImportJsonRequest):
        """Imports one session from the "Download all" JSON shape (see
        TrackingService.import_session_json/tracking.session_export) —
        the frontend's own batch-upload loop calls this once per session
        object found inside an uploaded .json file, same per-item
        try/except-and-continue convention it already uses per .txt file
        (see LabelProjectView.vue's own handleImportSession). No
        try/except here either: a malformed session raises
        TrackingServiceError (400), same global-handler convention as
        post_import_session above."""
        session_id = self.tracking_service.import_session_json(
            Session().user, self.project_service.get_active_project_name(), req.model_dump()
        )
        return {"success": True, "session_id": session_id}

    @get("/api/chat/sessions/export")
    def get_export_sessions(self):
        """The "Label sessions" view's own "Download all" button — every
        session (native and imported alike) of the active project, as one
        JSON array (see TrackingService.export_sessions/tracking.session_
        export's own module docstring on the exact shape). Same Response-
        with-Content-Disposition convention as get_project's own zip
        download — built server-side as bytes, not streamed, since a
        project's own session history is never large enough to need it."""
        project_name = self.project_service.get_active_project_name()
        payload = self.tracking_service.export_sessions(Session().user, project_name)
        content = json.dumps(payload, indent=2).encode("utf-8")
        encoded_project_name = quote(project_name)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=\"sessions.json\"; filename*=UTF-8''{encoded_project_name}-sessions.json"
            },
        )

    @delete("/api/chat/sessions/{session_id}")
    def delete_session(self, session_id: int):
        """Deletes a session and all its messages/signals — see
        ChatService.delete_session. Raises ChatServiceError (404) if it
        doesn't exist or belongs to someone else — handled by the global
        exception handler (see error_handlers.py), no try/except needed here."""
        self.chat_service.delete_session(session_id)
        return {"success": True}

    @put("/api/chat/sessions/{session_id}/labeled")
    def put_session_labeled(self, session_id: int, req: SetSessionLabeledRequest):
        """The "Label sessions" view's own "Mark done" button — see
        ChatService.mark_session_labeled. Raises ChatServiceError (404)
        for an unknown/not-yours session_id, same convention as
        delete_session above."""
        return self.chat_service.mark_session_labeled(session_id, req.labeled)

    @put("/api/chat/sessions/{session_id}/title")
    def put_session_title(self, session_id: int, req: SetSessionTitleRequest):
        """The "Label sessions" view's own Info tab — see ChatService.
        set_session_title. Same 404 convention as put_session_labeled."""
        return self.chat_service.set_session_title(session_id, req.title)

    @put("/api/chat/sessions/{session_id}/comment")
    def put_session_comment(self, session_id: int, req: CommentRequest):
        """The "Label sessions" view's own Info tab — see ChatService.
        set_session_comment (a whole-session note, distinct from
        put_message_comment's own per-message one below). Same 404
        convention as put_session_labeled."""
        return self.chat_service.set_session_comment(session_id, req.comment)

    @get("/api/chat/sessions/{session_id}/summary")
    def get_session_summary(self, session_id: int):
        """{content: str | None} — see ChatService.get_session_summary.
        Auto-queued the moment this session was discovered closed (see
        chat/session_summary_manager.py) — never triggered by this
        endpoint itself, which only ever reads."""
        return self.chat_service.get_session_summary(session_id)

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
        """The full Tracking event log for `session_id` (snapshots and
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

    @put("/api/chat/messages/{message_id}/comment")
    def put_message_comment(self, message_id: int, req: CommentRequest):
        """Sets or (comment: null/empty) clears message_id's expert-left
        free-text comment — the "Label sessions" view's per-message
        comment bubble. Unlike put_message_expected_state/
        put_message_expected_signals, every message is a legitimate
        target: no 409 here, only the usual 404 for an unowned/unknown
        message (handled globally, see error_handlers.py)."""
        return self.chat_service.set_message_comment(message_id, req.comment)

    @delete("/api/chat/sessions/{session_id}/annotations")
    def delete_session_annotations(self, session_id: int):
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Tracking rows —
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
        async with self.chat_service.lock:
            self.project_service.reset_active_project()
            self.chat_service.tracking_service.auto_tracking_enabled = True
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
            self.chat_service.tracking_service.auto_tracking_enabled = True
        return {"success": True}

    @get("/api/projects")
    def get_projects(self):
        return self.project_service.list_projects()

    @post("/api/projects/new")
    async def post_new_project(self):
        """"New project" — same effect as PUT /api/projects/{project_name}
        with backend/samples/Hello world.zip as the uploaded body, minus
        having to pick a project name first (see ProjectService.
        create_new_project's own de-duplication)."""
        return await self.project_service.create_new_project(self._activate_project)

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
        """Whether a Publish right now needs an explicit state remap first
        — see ProjectService.preview_publish. The Publish button's own
        confirm flow calls this before POSTing, to know whether to prompt
        for a remap target."""
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

    @post("/api/projects/{project_name}/benchmark-runs")
    def post_benchmark_run(self, project_name: str, req: CreateBenchmarkRunRequest):
        """Creates a BenchmarkRun and submits its replay job — returns
        immediately with status='pending' (see BenchmarkRunService.
        create_run), before the job's own worker thread has actually
        started it. BenchmarkServiceError (see metrics/benchmark_errors.py)
        is handled globally, see error_handlers.py."""
        try:
            return self.benchmark_run_service.create_run(
                Session().user, project_name, req.session_id, req.strategy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/benchmark-runs/{run_id}")
    def get_benchmark_run(self, project_name: str, run_id: int):
        """One BenchmarkRun, its own domain data merged with its Job's
        lifecycle (status/progress/error/timestamps) — see
        BenchmarkRunService.get_run. BenchmarkServiceError (404 for an
        unknown run_id) is handled globally, see error_handlers.py."""
        return self.benchmark_run_service.get_run(run_id)

    @get("/api/projects/{project_name}/benchmark-runs")
    def get_benchmark_runs(self, project_name: str, session_id: int | None = None):
        """Every BenchmarkRun for `project_name` with that exact
        session_id — None (the default) means every whole-project-scope
        run, not "no filter" (same convention as everywhere else in this
        system — see BenchmarkRunService.list_runs). Most recent first."""
        return self.benchmark_run_service.list_runs(project_name, session_id)

    @post("/api/projects/{project_name}/states/{state_key}/test")
    def post_state_test(self, project_name: str, state_key: str, req: StateTestRequest):
        """Launches the "Stati" branch's own aggregation job for one state
        — see BenchmarkRunService.start_job. Returns immediately with the
        ephemeral job's own id; poll GET .../state-jobs/{job_id} for its
        outcome (see get_state_job below)."""
        try:
            job_id = self.benchmark_run_service.start_job(Session().user, project_name, state_key, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"job_id": job_id}

    @get("/api/projects/{project_name}/state-jobs/{job_id}")
    def get_state_job(self, project_name: str, job_id: int):
        """One ephemeral 'state_aggregation' job's own status/progress/
        result — see BenchmarkRunService.get_job_status. None (never a
        404) for an unknown job_id, e.g. after a backend restart — the
        ephemeral queue's own JobSink already returns None for that case,
        distinguishable from "in progress"/"completed" by every caller."""
        return self.benchmark_run_service.get_job_status(job_id)

    @get("/api/projects/{project_name}/states")
    def get_project_states(self, project_name: str):
        """Every real state key of `project_name`'s current draft
        automaton — the "Stati" branch's own node list (see
        TestsTree.vue)."""
        try:
            return self.project_service.get_project_states(project_name)
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
    def get_project_signals(self, project_name: str, state_key: str | None = None):
        """Signal definitions (name/ui_label/description) of `project_name`'s
        last saved index.yml, for the "Edit project" view's Inspect panel —
        not restricted to the active project. `state_key`, when given,
        scopes each signal's own `relevant` field to that state's outgoing
        actions (see ProjectService.get_project_signals) — the Inspector's
        own currently selected/highlighted state."""
        try:
            return {"signals": self.project_service.get_project_signals(project_name, state_key)}
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_name}/env-keys")
    def get_project_env_keys(self, project_name: str):
        """Declared env-key definitions (name/ui_description/value) of
        `project_name`'s last saved index.yml, for the "Edit project"
        view's Inspect panel Env tab — not restricted to the active
        project (see ProjectService.get_project_env_keys)."""
        try:
            return {"env_keys": self.project_service.get_project_env_keys(project_name)}
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

    @get("/api/projects/{project_name}/files/{file_name}/content")
    def get_project_file_content(self, project_name: str, file_name: str, request: Request, session_id: int | None = None):
        """Raw bytes of `file_name`'s own content, for the two callers that
        can't use the JSON GET .../files/{file_name} above: ChatWindow.vue's
        own index.css skin-loading fetch (styles injected directly, no JSON
        envelope wanted) and the "Edit project" file explorer's own image
        preview (`<img>` src — the browser fetches this itself). `session_id`
        omitted resolves against the current draft (the editor's own case,
        same default as the JSON route); given, resolves the same revision
        that session's own automaton runs against (see ProjectService.
        get_project_file_content's own docstring for the live/'test'
        distinction). ETag'd off the content itself, so an unchanged file
        304s on a matching If-None-Match without ever touching the body."""
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
    # index.yml structural editing — add/edit/delete/reorder states,
    # actions, and signals without hand-writing YAML (see
    # AutomatonYamlEditor). Every one of these reuses put_project_file's
    # own validation/history/commit path (see ProjectService.
    # _edit_index_yml) — never a parallel write path of its own — and
    # returns only the affected object's own payload, never the whole
    # YAML text (see AutomatonBuilder.get_state_payload's equivalents,
    # StatePayload/ActionPayload/SignalPayload).
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
        """Every editable field of the init-action itself — see
        AutomatonYamlEditor.set_init_action_field. 'target' (moving the
        automaton's own start state) is the one case with its own
        validation (an unknown state name converts to 400, same as
        before this was generalized from its own dedicated .../target
        endpoint); every other field (e.g. 'ui-label') just writes
        through."""
        try:
            return await self.project_service.set_init_action_field(
                project_name, field, req.value, self._activate_project
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    # Named to sort alphabetically before put_action_field: route
    # registration order follows inspect.getmembers's own alphabetical
    # method-name order (see __init__ above), and FastAPI matches routes
    # in registration order — put_action_field's own {field} wildcard
    # would otherwise swallow this path's literal "order" segment as if
    # it were a field name, since "put_action_field" < "put_action_order"
    # lexicographically registers the wildcard route first.
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
            self.chat_service.tracking_service.auto_tracking_enabled = True
