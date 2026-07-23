"""FastAPI entrypoint for the Avance State Engine prototype."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

from automaton import Automaton
from chat_service import ChatService, ChatServiceError
from conversation_controller import ConversationController
from db import db
from models_manager import models_manager
from providers.factory import build_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_CHAT_TRANSPORTS = ("websocket", "rest")
CHAT_TRANSPORT = os.environ.get("CHAT_TRANSPORT", "websocket").strip().lower()
if CHAT_TRANSPORT not in _CHAT_TRANSPORTS:
    raise RuntimeError(
        f"CHAT_TRANSPORT={CHAT_TRANSPORT!r} is not valid. Allowed values: {', '.join(_CHAT_TRANSPORTS)}. "
        "Set it in .env."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Covers the very first server boot: generate the opening message
    # before serving, if the conversation is already empty. Every other
    # empty-conversation case goes through _activate_and_reset() instead.
    # Always chat_service directly (never conversation_controller, which
    # may not even exist under CHAT_TRANSPORT=rest) — generating and
    # persisting the opening message doesn't depend on any transport.
    await chat_service.open_if_needed()
    yield


app = FastAPI(title="Avance State Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # FIXME: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_body(message: str, detail: str | None = None) -> dict:
    return {"error": {"message": message, "detail": detail}}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Most routes raise HTTPException(detail=str(...)) — a single readable
    # string with no separate technical detail. ChatServiceError (see POST
    # /api/messages below) has both, so its route packs them into a dict
    # detail instead, recognized here rather than string-only everywhere.
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.detail["message"], exc.detail.get("detail"))
        )
    return JSONResponse(status_code=exc.status_code, content=_error_body(str(exc.detail)))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content=_error_body("Internal server error.", str(exc)))

llm_provider = build_provider()

class ActionRequest(BaseModel):
    action_name: str


class AutoTrackingRequest(BaseModel):
    enabled: bool


class TriggersPreviewRequest(BaseModel):
    signals: dict[str, int | None]


class ChatMessageRequest(BaseModel):
    message: str


def _state_payload() -> dict:
    return models_manager.get_active_automaton().get_current_state_payload()


# The transport-agnostic chat-turn logic (see chat_service.py) — shared by
# both /ws/chat and POST /api/messages, always constructed regardless of
# CHAT_TRANSPORT: POST /api/messages is always mounted, and open_if_needed()
# (lifespan, above) always needs it too.
chat_service = ChatService(
    llm_provider,
    models_manager.get_active_automaton,
)


@app.get("/api/signals")
def get_signals():
    """Read-only: never calls the AI. Signals are only (re)computed inside
    the auto-tracking flow (see ChatService._run_auto_tracking); this just
    reports the latest persisted snapshot."""
    return chat_service.signals.get_latest_signals()


@app.get("/api/state")
def get_state():
    return _state_payload()


@app.get("/api/messages")
def get_messages():
    """Persisted conversation history, for the frontend to redisplay after a
    reload/backend restart — including the opening message, regardless of
    which chat transport (if any) is available for turns."""
    return chat_service.get_messages()


@app.post("/api/messages")
async def post_message(req: ChatMessageRequest):
    """Synchronous REST alternative to /ws/chat, always mounted regardless
    of CHAT_TRANSPORT — the frontend's always-available fallback once it's
    determined the websocket isn't. No retrying-progress notifications
    (see ChatService.process_turn's on_retry): retries still happen
    server-side, just silently from this transport's point of view."""
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    try:
        result = await chat_service.process_turn(text)
    except ChatServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"message": exc.message, "detail": exc.detail}
        ) from exc
    return {
        "reply": result.reply,
        "state": result.state,
        "state_changed": result.state_changed,
        "new_state": result.new_state,
        "triggered_action": result.triggered_action,
    }


# /ws/chat is only registered when the websocket transport isn't excluded
# — under CHAT_TRANSPORT=rest, a connection attempt gets FastAPI's natural
# 404 for a route that was never mounted, no custom rejection logic needed.
if CHAT_TRANSPORT == "websocket":
    conversation_controller = ConversationController(chat_service)

    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket):
        await conversation_controller.chat_loop(websocket)


@app.post("/api/action")
def post_action(req: ActionRequest):
    automaton = models_manager.get_active_automaton()
    from_state = automaton.get_current_state().key
    try:
        new_state = automaton.apply_action(req.action_name).target
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    automaton.log_transition(from_state, new_state, req.action_name, "manual")
    db.save_transition(from_state, req.action_name, new_state, None)
    return _state_payload()


@app.get("/api/autotracking")
def get_autotracking():
    return {"enabled": chat_service.auto_tracking_enabled}


@app.post("/api/autotracking")
def post_autotracking(req: AutoTrackingRequest):
    chat_service.auto_tracking_enabled = req.enabled
    return {"enabled": chat_service.auto_tracking_enabled}


@app.post("/api/triggers/preview")
def post_triggers_preview(req: TriggersPreviewRequest):
    """For SignalsView's 'next triggerable action' panel: evaluates triggers
    for the current state against signal values the frontend already has
    (from GET /api/signals) — no AI call, never applies a transition."""
    return models_manager.get_active_automaton().preview_triggers(req.signals)


@app.post("/api/reset")
async def post_reset():
    # Routed through the same commit callback every model-lifecycle
    # operation uses — Reset doesn't change the active model, but gets the
    # same opening-message behavior for free rather than a separate path.
    await _activate_and_reset(models_manager.get_active_automaton())
    return _state_payload()


async def _activate_and_reset(new_automaton: Automaton) -> None:
    """The commit callback for every model-lifecycle activation (and POST
    /api/reset's plain conversation-clear). Resets DB/automaton state/
    auto-tracking and persists the active model name, under chat_service.lock
    (shared with both chat transports)."""
    async with chat_service.lock:
        db.reset_all()
        new_automaton.set_current_state(new_automaton.initial_state)
        # models_manager already swapped _active_model_name for this commit
        # (or left it unchanged for POST /api/reset) — either way this is
        # the current active model, persisted for the next boot.
        db.set_active_model_name(models_manager.get_active_model_name())
        chat_service.auto_tracking_enabled = True
        await chat_service.open_if_needed()


@app.get("/api/models")
def get_models():
    return models_manager.list_models()


@app.put("/api/models/{model_name}/activate")
async def activate_model(model_name: str):
    """Activates an already-present model, validated via the same
    AutomatonBuilder as boot/upload/delete. Idempotent: re-activating the
    active model still validates, but skips the swap + conversation reset."""
    try:
        await models_manager.activate_model_idempotent(model_name, _activate_and_reset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "model_name": model_name}


@app.get("/api/models/{model_name}")
def get_model(model_name: str):
    """Downloads `model_name` as a zip — the read side of PUT
    /api/models/{model_name}, built so it round-trips back through PUT with
    no transformation. Not restricted to the active model."""
    try:
        content = models_manager.export_model_zip(model_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{model_name}.zip"'},
    )


@app.put("/api/models/{model_name}")
async def put_model(model_name: str, request: Request):
    """Creates or replaces `model_name` from a raw body (YAML or zip, see
    models_manager._looks_like_zip). Stage -> validate -> only on success
    commit and swap, running the same reset as POST /api/reset."""
    content = await request.body()
    content_type = request.headers.get("content-type")
    try:
        return await models_manager.put_model(model_name, content, content_type, _activate_and_reset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/models/{model_name}")
async def delete_model(model_name: str):
    """Removes models/<model_name>/ from disk — any model except "default"
    (PermissionError). If it was the active model, falls back to "default"
    via activate_model()."""
    try:
        await models_manager.delete_model(model_name, _activate_and_reset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True}
