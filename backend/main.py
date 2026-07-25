"""FastAPI entrypoint for the Avance State Engine prototype — env/wiring
only. Every endpoint lives on AvanceController (see controller.py)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from automaton.automaton import Automaton
from chat_service import ChatService
from chat_ws_adapter import ChatWsAdapter
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from model_service import ModelService
from model_watcher import ModelWatcher
from ai import provider_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_CHAT_TRANSPORTS = ("websocket", "rest")
CHAT_TRANSPORT = os.environ.get("CHAT_TRANSPORT", "rest").strip().lower()
if CHAT_TRANSPORT not in _CHAT_TRANSPORTS:
    raise RuntimeError(
        f"CHAT_TRANSPORT={CHAT_TRANSPORT!r} is not valid. Allowed values: {', '.join(_CHAT_TRANSPORTS)}. "
        "Set it in .env."
    )

llm_provider = os.environ.get("LLM_PROVIDER", "gemini").strip()
llm_api_key = os.environ.get("LLM_API_KEY", "").strip()
llm_name = os.environ.get("LLM_NAME", "gemini-flash-lite-latest").strip()
database_url = os.environ.get("DATABASE_URL", "sqlite:///avance.db")
MODEL_FILE_WATCH_ENABLED = os.environ.get("MODEL_FILE_WATCH_ENABLED", "false").strip().lower() == "true"

llm_provider = provider_factory.make(llm_provider, api_key=llm_api_key, model=llm_name)
db = Db(database_url)
models_manager = ModelService(db)
chat_service = ChatService(llm_provider, models_manager, db)

chat_ws_adapter = ChatWsAdapter(chat_service) if CHAT_TRANSPORT == "websocket" else None

model_watcher: ModelWatcher | None = None
if MODEL_FILE_WATCH_ENABLED:
    async def _on_watcher_commit(new_automaton: Automaton) -> None:
        # Unused: kept only to match ModelService's CommitCallback shape,
        # same as AvanceController._activate_model — re-enables
        # auto-tracking for whichever model this reset was for.
        async with chat_service.lock:
            chat_service.auto_tracking_enabled = True

    _on_active_model_reset = chat_ws_adapter.push_model_updated if chat_ws_adapter else None
    model_watcher = ModelWatcher(models_manager, _on_watcher_commit, _on_active_model_reset)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if model_watcher is not None:
        model_watcher.start()
    yield
    if model_watcher is not None:
        model_watcher.stop()


app = FastAPI(title="Avance State Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # FIXME: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

controller = AvanceController(chat_service, models_manager)
app.include_router(controller.router)

if chat_ws_adapter is not None:
    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket):
        await chat_ws_adapter.chat_loop(websocket)
