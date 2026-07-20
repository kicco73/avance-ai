"""FastAPI entrypoint for the Avance State Engine prototype."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from automaton import load_automaton
from llm_provider import LLMProviderError, LLMProviderRateLimitedError, LLMProviderUnavailableError
from providers.factory import build_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATE_MACHINE_PATH = Path(__file__).parent / "state_machine.yml"

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
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

automaton = load_automaton(STATE_MACHINE_PATH)
llm_provider = build_provider()


class SessionState:
    def __init__(self, initial_state: str):
        self.current_state: str = initial_state
        self.history: list[dict] = []

    def reset(self, initial_state: str) -> None:
        self.current_state = initial_state
        self.history = []


session = SessionState(automaton.initial_state)


class ActionRequest(BaseModel):
    action_name: str


# Single-user prototype: serializes chat processing across all websocket
# connections so two concurrent sends can't race on `session`.
chat_lock = asyncio.Lock()


async def _process_chat_message(text: str, send) -> None:
    """Runs one chat turn, pushing status updates via `send` as they occur.

    `send` is an async callable taking a JSON-serializable dict (typically
    a websocket's `send_json`). Retry/backoff timing lives entirely here.
    """
    session.history.append({"role": "user", "content": text})

    state = automaton.get_state(session.current_state)
    if state.fixed_message:
        logger.warning("Translating fixed_message for state '%s'.", state.key)
        system_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
    else:
        system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_instructions}"

    attempt = 0
    while True:
        try:
            reply = await asyncio.to_thread(llm_provider.generate, system_prompt, session.history)
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
            await send({"type": "failed", "error": str(exc)})
            return
        except LLMProviderError as exc:
            # Not retryable: remove the unanswered user message.
            session.history.pop()
            await send({"type": "failed", "error": str(exc)})
            return

        session.history.append({"role": "assistant", "content": reply})
        await send({"type": "done", "reply": reply, "state": _state_payload()})
        return


def _state_payload() -> dict:
    state = automaton.get_state(session.current_state)
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


@app.get("/api/state")
def get_state():
    return _state_payload()


@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            text = (data or {}).get("message", "").strip()
            if not text:
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
    try:
        new_state = automaton.apply_action(session.current_state, req.action_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.current_state = new_state
    return _state_payload()


@app.post("/api/reset")
def post_reset():
    session.reset(automaton.initial_state)
    return _state_payload()
