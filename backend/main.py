"""FastAPI entrypoint for the Avance State Engine prototype — config/wiring
only. Every endpoint lives on AvanceController (see controller.py)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from automaton.automaton import Automaton
from chat.chat_service import ChatService
from chat.ws_adapter import WsAdapter
from config import AppConfig
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from model_service import ModelService
from model_watcher import ModelWatcher
from ai.ai_service import AiService
from audio.audio_service import AudioService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

config = AppConfig()

ai_service = AiService(config.ai_services)
audio_service = AudioService(config.audio_services)
db = Db(config.database_url)
model_service = ModelService(db)
chat_service = ChatService(ai_service, model_service, db)

chat_ws_adapter = WsAdapter(chat_service) if config.chat_transport == "websocket" else None

model_watcher: ModelWatcher | None = None
if config.model_file_watch_enabled:
    async def _on_watcher_commit(new_automaton: Automaton) -> None:
        # Unused: kept only to match ModelService's CommitCallback shape,
        # same as AvanceController._activate_model — re-enables
        # auto-tracking for whichever model this reset was for.
        async with chat_service.lock:
            chat_service.auto_tracking_enabled = True

    _on_active_model_reset = chat_ws_adapter.push_model_updated if chat_ws_adapter else None
    model_watcher = ModelWatcher(model_service, _on_watcher_commit, _on_active_model_reset)


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

controller = AvanceController(chat_service, model_service, audio_service)
app.include_router(controller.router)

if chat_ws_adapter is not None:
    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket):
        await chat_ws_adapter.chat_loop(websocket)
