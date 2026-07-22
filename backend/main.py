"""FastAPI entrypoint for the Avance State Engine prototype."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

import db
import models_manager
import signals
from automaton import Automaton, trigger_signal_names
from llm_provider import LLMProviderError, LLMProviderRateLimitedError, LLMProviderUnavailableError
from providers.factory import build_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Retry/backoff policy for transient upstream overload (HTTP 503). Owned
# entirely by the backend: the frontend only renders the status messages
# pushed over the websocket, it never decides whether/when to retry.
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0

# System prompt used for states with a `fixed_message` (e.g. crisis): the
# model must translate it verbatim, not generate a free-form reply.
FIXED_MESSAGE_INSTRUCTIONS = (
    "You must reply with ONLY a translation of the fixed message below into "
    "the same language the user's last message is written in. Do not answer "
    "or react to what the user said, do not add or remove anything, and do "
    "not change its meaning or formatting — output just the translation.\n\n"
    "Fixed message:\n{fixed_message}"
)

app = FastAPI(title="Avance State Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # FIXME: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

models_manager.init_default_model()
llm_provider = build_provider()
db.init_db()


class SessionState:
    """In-memory working copy used for the actual per-turn LLM calls — kept
    for simplicity/speed, but now backed by the database: hydrated from it
    at startup, and dual-written to it as messages/transitions occur."""

    def __init__(self, initial_state: str):
        self.current_state: str = db.get_current_state(initial_state)
        self.history: list[dict] = db.get_all_messages()
        self.auto_tracking_enabled: bool = True

    def reset(self, initial_state: str) -> None:
        db.reset_all()
        self.current_state = initial_state
        self.history = []
        self.auto_tracking_enabled = True


session = SessionState(models_manager.get_active_automaton().initial_state)


class ActionRequest(BaseModel):
    action_name: str


class AutoTrackingRequest(BaseModel):
    enabled: bool


class TriggersPreviewRequest(BaseModel):
    signals: dict[str, int | None]


# Single-user prototype: serializes chat processing across all websocket
# connections so two concurrent sends can't race on `session`.
chat_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_timestamps(history: list[dict]) -> list[dict]:
    """`LLMProvider.generate` only knows {role, content} — timestamps are
    tracked in `session.history` for /api/signals, not sent to the model
    during normal chat."""
    return [{"role": m["role"], "content": m["content"]} for m in history]


def _build_priming_messages(attachments: list) -> list[dict]:
    """The (never persisted — neither in memory nor in the DB) turn carrying
    this call's attachments as provider-neutral 'attachment' content blocks.
    Rebuilt fresh on every single call from whatever's in scope right now
    (general_prompt + current state for chat, per-signal for signals
    computation) — never reused across calls, so a state change is reflected
    immediately. Each LLMProvider renders these its own way: AnthropicProvider
    turns them into real `document` blocks with a cache breakpoint;
    GeminiProvider falls back to concatenating text-only attachments."""
    if not attachments:
        return []
    return [
        {
            "role": "user",
            "content": [
                {"type": "attachment", "filename": a.filename, "source": a.source}
                for a in attachments
            ],
        },
        {"role": "assistant", "content": "Understood."},
    ]


def _log_transition(
    from_state: str,
    to_state: str,
    action_name: str,
    trigger_type: str,
    signal_values: dict | None = None,
) -> None:
    """Logs every state transition (manual or auto) at the destination
    state's configured `transition_log_level` (default WARNING)."""
    level = getattr(logging, models_manager.get_active_automaton().get_state(to_state).transition_log_level)
    message = f"State transition: {from_state} -> {to_state} (action={action_name}, trigger={trigger_type})"
    if signal_values:
        message += f" signals={signal_values}"
    logger.log(level, message)


async def _run_auto_tracking() -> tuple[bool, str | None, str | None]:
    """Computes signals and applies the first matching trigger for the
    current state, if auto-tracking is on. Runs BEFORE the conversational
    reply is generated, so that reply is produced under the destination
    state's prompt (e.g. an acute-risk message must get the crisis
    fixed_message translation, not one more turn of the old state's prompt).

    Returns (state_changed, new_state, triggered_action).
    """
    if not session.auto_tracking_enabled:
        return False, None, None

    automaton = models_manager.get_active_automaton()
    signals_list = await signals.compute_signals(llm_provider, session.history, _build_priming_messages)
    signal_values = {s["name"]: s["value"] for s in signals_list}
    # Saved before trigger evaluation so a fired transition can reference
    # the exact snapshot id that caused it.
    snapshot_id = db.save_signal_snapshot(signal_values)

    triggered_action = automaton.evaluate_triggers(session.current_state, signal_values)
    if triggered_action is None:
        return False, None, None

    from_state = session.current_state
    new_state_key = automaton.apply_action(from_state, triggered_action)
    session.current_state = new_state_key
    fired_action = next(
        a for a in automaton.get_state(from_state).actions if a.name == triggered_action
    )
    relevant_names = trigger_signal_names(fired_action.trigger)
    relevant_values = {n: signal_values.get(n) for n in relevant_names}
    _log_transition(from_state, new_state_key, triggered_action, "auto", relevant_values)
    db.save_transition(from_state, triggered_action, new_state_key, snapshot_id)
    return True, new_state_key, triggered_action


async def _process_chat_message(text: str, send) -> None:
    """Runs one chat turn, pushing status updates via `send` as they occur.

    `send` is an async callable taking a JSON-serializable dict (typically
    a websocket's `send_json`). Retry/backoff timing lives entirely here.
    """
    session.history.append({"role": "user", "content": text, "timestamp": _now_iso()})

    state_changed, new_state_key, triggered_action = await _run_auto_tracking()

    automaton = models_manager.get_active_automaton()
    state = automaton.get_state(session.current_state)
    if state.fixed_message:
        logger.warning("Translating fixed_message for state '%s'.", state.key)
        system_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
        # A pure translation task doesn't use contextual_prompt, so it
        # doesn't carry the attachments meant for it either.
        turn_attachments = []
    else:
        system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}"
        turn_attachments = automaton.general_prompt_attachments + state.attachments

    priming_messages = _build_priming_messages(turn_attachments)
    chat_history = priming_messages + _strip_timestamps(session.history)
    transition_fields = {
        "state_changed": state_changed,
        "new_state": new_state_key,
        "triggered_action": triggered_action,
    }

    attempt = 0
    while True:
        try:
            reply = await asyncio.to_thread(llm_provider.generate, system_prompt, chat_history)
        except LLMProviderUnavailableError as exc:
            logger.error(
                "LLM provider temporarily unavailable (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES + 1,
                exc,
            )
            if attempt >= MAX_RETRIES:
                session.history.pop()
                await send({
                    "type": "failed",
                    "error": f"Service unavailable after {MAX_RETRIES} retries: {exc}",
                    **transition_fields,
                })
                return
            attempt += 1
            remaining = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
            while remaining > 0:
                await send({
                    "type": "retrying",
                    "attempt": attempt,
                    "max_attempts": MAX_RETRIES,
                    "retry_in": round(remaining, 1),
                })
                step = min(1.0, remaining)
                await asyncio.sleep(step)
                remaining -= step
            continue
        except LLMProviderRateLimitedError as exc:
            logger.critical("LLM provider rate limit exceeded: %s", exc)
            session.history.pop()
            await send({"type": "failed", "error": str(exc), **transition_fields})
            return
        except LLMProviderError as exc:
            # Not retryable: remove the unanswered user message.
            session.history.pop()
            await send({"type": "failed", "error": str(exc), **transition_fields})
            return

        session.history.append({"role": "assistant", "content": reply, "timestamp": _now_iso()})
        # Persisted only once the turn is fully successful, mirroring the
        # in-memory pop-on-failure above: memory and DB never diverge — a
        # message pair is either both persisted, or neither.
        db.save_message("user", text)
        db.save_message("assistant", reply)
        await send({
            "type": "done",
            "reply": reply,
            "state": _state_payload(),
            **transition_fields,
        })
        return


def _state_payload() -> dict:
    state = models_manager.get_active_automaton().get_state(session.current_state)
    return {
        "key": state.key,
        "label": state.label,
        "description": state.description,
        "final": state.final,
        "actions": [
            {
                "name": a.name,
                "label": a.label,
                "button_text": a.button_text,
                "target": a.target,
            }
            for a in state.actions
        ],
    }


@app.get("/api/signals")
def get_signals():
    """Read-only: never calls the AI. Signals are only (re)computed inside
    the auto-tracking flow in _process_chat_message (see signals.py); this
    just reports the latest persisted snapshot."""
    return signals.get_latest_signals()


@app.get("/api/state")
def get_state():
    return _state_payload()


@app.get("/api/messages")
def get_messages():
    """Persisted conversation history, for the frontend to redisplay after a
    reload/backend restart — session.history itself is only ever used
    internally to build LLM calls, never sent to the client directly."""
    return db.get_all_messages()


@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            text = (data or {}).get("message", "").strip()
            if not text:
                continue
            if models_manager.get_active_automaton().get_state(session.current_state).final:
                # Final states are terminal by design: no message the client
                # could have already queued should reach the model, no matter
                # how the state got here (manual button or auto-tracking).
                await websocket.send_json({
                    "type": "failed",
                    "error": "The conversation has ended in this state.",
                })
                continue
            if chat_lock.locked():
                await websocket.send_json({
                    "type": "error",
                    "error": "A chat reply is already being generated.",
                })
                continue
            async with chat_lock:
                await _process_chat_message(text, websocket.send_json)
    except WebSocketDisconnect:
        pass


@app.post("/api/action")
def post_action(req: ActionRequest):
    from_state = session.current_state
    try:
        new_state = models_manager.get_active_automaton().apply_action(session.current_state, req.action_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.current_state = new_state
    _log_transition(from_state, new_state, req.action_name, "manual")
    db.save_transition(from_state, req.action_name, new_state, None)
    return _state_payload()


@app.get("/api/autotracking")
def get_autotracking():
    return {"enabled": session.auto_tracking_enabled}


@app.post("/api/autotracking")
def post_autotracking(req: AutoTrackingRequest):
    session.auto_tracking_enabled = req.enabled
    return {"enabled": session.auto_tracking_enabled}


@app.post("/api/triggers/preview")
def post_triggers_preview(req: TriggersPreviewRequest):
    """For SignalsView's 'next triggerable action' panel: evaluates triggers
    for the current state against signal values the frontend already has
    (from GET /api/signals) — no AI call, never applies a transition."""
    return models_manager.get_active_automaton().preview_triggers(session.current_state, req.signals)


@app.post("/api/reset")
def post_reset():
    session.reset(models_manager.get_active_automaton().initial_state)
    return _state_payload()


async def _activate_and_reset(new_automaton: Automaton) -> None:
    """The commit callback passed into every models_manager entry point that
    activates a new automaton (activate, create-or-replace). models_manager
    owns *which* automaton is active; this owns resetting *this process's
    chat session* (DB/history/auto-tracking) to match it, under chat_lock so
    it can never race an in-flight chat turn. Shared by both
    PUT /api/models/{model_name}/activate and PUT /api/models/{model_name}
    so that reset logic is never duplicated between them — and, since it's
    only ever called when the active model actually changes, activating the
    same model twice in a row never repeats this reset either.
    """
    async with chat_lock:
        session.reset(new_automaton.initial_state)


@app.get("/api/models")
def get_models():
    return models_manager.list_models()


@app.put("/api/models/{model_name}/activate")
async def activate_model(model_name: str):
    """Activates an already-present model under models/<model_name>/,
    validating it with the exact same load_automaton() used at boot/upload/
    delete. No filesystem writes are involved — the files are already there
    — so a failed validation leaves everything untouched. Idempotent:
    activating the model that's already active still validates it, but
    skips the swap and the session reset entirely (see
    models_manager.activate_model_idempotent)."""
    try:
        await models_manager.activate_model_idempotent(model_name, _activate_and_reset)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "model_name": model_name}


@app.get("/api/models/{model_name}")
def get_model(model_name: str):
    """Downloads `model_name` as a zip — the read side of the same resource
    PUT /api/models/{model_name} writes. Always a zip (even for a model with
    no attachments), built with the exact flat layout PUT already requires,
    so the file this returns is accepted back by PUT with no transformation
    at all. Not restricted to the active model, consistent with DELETE
    already being general-purpose on this resource."""
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
    """Creates or fully replaces the model named `model_name` from the raw
    request body — either a lone YAML file or a zip bundle (index.yml +
    attachments, flat), told apart by Content-Type (with a magic-number
    fallback; see models_manager._looks_like_zip). Unlike the old filename-
    based upload, the resource name comes only from the URL, never from
    anything in the body. Stage -> validate (via the exact same
    load_automaton() used at boot) -> only on success, commit into
    `models/<model_name>/` and swap the active automaton, running the exact
    same reset already used by POST /api/reset. A failed validation leaves
    the filesystem, the active automaton, and the DB exactly as they were —
    nothing to roll back because nothing was changed."""
    content = await request.body()
    content_type = request.headers.get("content-type")
    return await models_manager.put_model(model_name, content, content_type, _activate_and_reset)


@app.delete("/api/models/{model_name}")
async def delete_model(model_name: str):
    """Removes models/<model_name>/ from disk. Any listed model can be
    deleted, active or not — deleting an unused one has zero effect on the
    in-memory automaton or the DB. Deleting the currently active model falls
    back to "default" via the same activate_model()/reset already used by
    the activate and put endpoints. The default model itself can never be deleted — enforced
    in models_manager regardless of what any caller does, not just this
    endpoint."""
    try:
        await models_manager.delete_model(model_name, _activate_and_reset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True}
