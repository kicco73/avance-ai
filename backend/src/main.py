"""FastAPI entrypoint for the Avance State Engine prototype — config/wiring
only. Every endpoint lives on AvanceController (see controller.py)."""
from __future__ import annotations
from importlib.metadata import PackageNotFoundError, version
import logging
from http import HTTPStatus

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.ws_adapter import WsAdapter
from config import AppConfig
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from ai.ai_service import AiService
from session import Session
from tracking.tracking_service import TrackingService
from talk.talk_service import TalkService
from listen.listen_service import ListenService

try:
    __version__ = version("avance-ai-backend")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
logger.info(f"Booting avance api server v{__version__}.")

def _build_fallback_app(error: Exception) -> FastAPI:
    """Used only when essential startup wiring below fails: every request,
    to any path or method, gets the same {error: {message, detail}} shape
    error_handlers.py already produces for a normal request failure — so
    the frontend renders it exactly like any other backend error instead
    of just failing to connect with no explanation.
    """
    fallback_app = FastAPI(title="Avance State Engine (misconfigured)")
    fallback_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    body = {"error": {"message": "The backend is not configured correctly.", "detail": str(error)}}

    @fallback_app.api_route(
        "/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    )
    async def catch_all(full_path: str):
        return JSONResponse(status_code=HTTPStatus.SERVICE_UNAVAILABLE, content=body)

    return fallback_app


try:
    config = AppConfig()

    ai_service = AiService.from_config(config.ai_services)
    talk_service = TalkService.from_config(config.talk_services) if config.talk_services is not None else None
    listen_service = ListenService.from_config(config.listen_services) if config.listen_services is not None else None
    db = Db(
        config.database_url,
        force_drop_and_create_when_incompatible=config.database_force_drop_and_create_when_incompatible,
    )
    project_service = ProjectService(db)
    session_manager = ChatSessionManager(db, open_window_minutes=config.max_session_duration_in_minutes)
    # A leaf service (see metrics/metric_service.py's own module
    # docstring) — depends only on db, so it's built first and handed to
    # whoever needs it, never the other way around.
    metric_service = MetricService(
        db,
        get_username=lambda: Session().user,
        get_active_project_name=lambda: project_service.get_active_project_name(),
        get_max_session_duration_in_minutes=lambda: config.max_session_duration_in_minutes,
    )
    # Architecturally analogous to ai_service/chat_service/... above —
    # instantiated once here, not built by ChatService for itself (see
    # tracking/tracking_service.py's own module docstring). Both this and
    # ChatService depend on ai_service (and metric_service) directly,
    # never through one another.
    tracking_service = TrackingService(
        db, ai_service, metric_service,
        get_active_automaton=lambda: project_service.get_active_automaton_and_state()[0],
        get_username=lambda: Session().user,
        get_active_project_name=lambda: project_service.get_active_project_name(),
    )
    chat_service = ChatService(ai_service, project_service, db, session_manager, tracking_service, metric_service)

    chat_ws_adapter = WsAdapter(chat_service) if config.chat_transport == "websocket" else None

    app = FastAPI(title="Avance State Engine")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], # FIXME: restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    controller = AvanceController(chat_service, project_service, talk_service, listen_service, db)
    app.include_router(controller.router)

    if chat_ws_adapter is not None:
        adapter = chat_ws_adapter

        @app.websocket("/ws/chat")
        async def chat_ws(websocket: WebSocket) -> None:
            await adapter.chat_loop(websocket)
        
    logger.info("Boot completed - avance api server ready.")

except Exception as exc:
    logger.exception("Backend failed to start — serving a fallback error app instead of crashing.")
    app = _build_fallback_app(exc)

