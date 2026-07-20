"""FastAPI entrypoint for the Avance State Engine prototype."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from automaton import load_automaton
from llm_provider import LLMProviderError, LLMProviderUnavailableError
from providers.factory import build_provider

STATE_MACHINE_PATH = Path(__file__).parent / "state_machine.yml"
CRISIS_STATE_KEY = "crisis"

# Retry/backoff policy for transient upstream overload (HTTP 503). Owned
# entirely by the backend: the frontend only polls job status and displays
# it, it never decides whether/when to retry.
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0

# Fixed general instructions, combined with the current state's contextual_prompt.
GENERAL_INSTRUCTIONS = (
    "You are part of a harm-reduction prototype application for alcohol use, "
    "following the Transtheoretical Model of Change and Relapse Prevention frameworks. "
    "Always respond in the same language the user writes in. You are not a substitute "
    "for professional medical, psychological, or psychiatric care. Keep responses "
    "conversational, warm, and concise (a few sentences, not an essay)."
)

# PLACEHOLDER — TO BE REPLACED with crisis resources reviewed and validated by a
# licensed clinical team before any real-world use. These Spanish resources are
# included only as a plausible example for this prototype and must not be relied
# upon as accurate, current, or clinically vetted.
CRISIS_FIXED_MESSAGE = (
    "Lo que describes suena serio, y quiero asegurarme de que tengas apoyo inmediato.\n\n"
    "Este prototipo no puede continuar la conversación en este estado: si estás en peligro "
    "inmediato o pensando en hacerte daño, por favor contacta ahora mismo con:\n\n"
    "- Emergencias: 112\n"
    "- Teléfono contra el Suicidio (España): 024\n"
    "- Teléfono de la Esperanza: 717 003 717\n\n"
    "[PROTOTYPE PLACEHOLDER — TO BE REPLACED with clinically validated crisis resources "
    "before any real-world deployment.]"
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


class ChatRequest(BaseModel):
    message: str


class ActionRequest(BaseModel):
    action_name: str


@dataclass
class ChatJob:
    id: str
    message: str
    status: str = "pending"  # pending -> retrying -> done | failed
    attempt: int = 0
    retry_at: float | None = None
    reply: str | None = None
    state: dict | None = None
    error: str | None = None


# Single-user prototype: at most one chat job is ever in flight at a time.
chat_jobs: dict[str, ChatJob] = {}


def _job_payload(job: ChatJob) -> dict:
    payload = {
        "job_id": job.id,
        "status": job.status,
        "attempt": job.attempt,
        "max_attempts": MAX_RETRIES,
    }
    if job.status == "retrying" and job.retry_at is not None:
        payload["retry_in"] = max(0.0, round(job.retry_at - time.time(), 1))
    if job.status == "done":
        payload["reply"] = job.reply
        payload["state"] = job.state
    if job.status == "failed":
        payload["error"] = job.error
    return payload


async def _run_chat_job(job: ChatJob) -> None:
    session.history.append({"role": "user", "content": job.message})

    if session.current_state == CRISIS_STATE_KEY:
        reply = CRISIS_FIXED_MESSAGE
        session.history.append({"role": "assistant", "content": reply})
        job.reply = reply
        job.state = _state_payload()
        job.status = "done"
        return

    state = automaton.get_state(session.current_state)
    system_prompt = f"{state.contextual_prompt}\n\n{GENERAL_INSTRUCTIONS}"

    attempt = 0
    while True:
        try:
            reply = await asyncio.to_thread(llm_provider.generate, system_prompt, session.history)
        except LLMProviderUnavailableError as exc:
            if attempt >= MAX_RETRIES:
                session.history.pop()
                job.status = "failed"
                job.error = f"Service unavailable after {MAX_RETRIES} retries: {exc}"
                return
            attempt += 1
            job.attempt = attempt
            job.status = "retrying"
            delay = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
            job.retry_at = time.time() + delay
            await asyncio.sleep(delay)
            continue
        except LLMProviderError as exc:
            # Not retryable: remove the unanswered user message.
            session.history.pop()
            job.status = "failed"
            job.error = str(exc)
            return

        session.history.append({"role": "assistant", "content": reply})
        job.reply = reply
        job.state = _state_payload()
        job.status = "done"
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


@app.post("/api/chat", status_code=202)
async def post_chat(req: ChatRequest):
    if any(job.status in ("pending", "retrying") for job in chat_jobs.values()):
        raise HTTPException(status_code=409, detail="A chat reply is already being generated.")

    job = ChatJob(id=str(uuid.uuid4()), message=req.message)
    chat_jobs[job.id] = job
    asyncio.create_task(_run_chat_job(job))
    return _job_payload(job)


@app.get("/api/chat/{job_id}")
def get_chat_job(job_id: str):
    job = chat_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return _job_payload(job)


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
    chat_jobs.clear()
    return _state_payload()
