"""FastAPI entrypoint for the Avance State Engine prototype — config/wiring
only. Every endpoint lives on AvanceController (see controller.py)."""

from __future__ import annotations
import inspect
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth.auth_middleware import AuthMiddleware
from auth.auth_service import AuthService
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.ws_adapter import WsAdapter
from config import AppConfig
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from jobs import InMemoryJobSink, JobQueue, PersistedJobSink
from metrics.benchmark_run_service import BenchmarkRunService
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from ai.ai_service import AiService
from tracking.tracking_service import TrackingService
from tracking.wakeup_service import WakeupService
from talk.talk_service import TalkService
from listen.listen_service import ListenService

__version__ = "1.7.1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _build_fallback_app(error: Exception) -> FastAPI:
    """Used only when essential startup wiring fails: every request gets
    the same {error: {message, detail}} shape error_handlers.py produces
    for a normal failure, so the frontend renders it like any other error.
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


def create_app() -> FastAPI:
    config = AppConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # --- STARTUP ---
        logger.info(f"Booting avance headless server v{__version__}.")
        
        ai_service = AiService.from_config(config.ai_services)
        talk_service = TalkService.from_config(config.talk_services) if config.talk_services is not None else None
        listen_service = ListenService.from_config(config.listen_services) if config.listen_services is not None else None
        
        db = Db(
            config.database_url,
            force_drop_and_create_when_incompatible=config.database_force_drop_and_create_when_incompatible,
        )

        # Built once here (not a global singleton — see auth/auth_service.py's
        # own module docstring), passed explicitly to whatever needs it.
        # Also bridged onto app.state: AuthMiddleware was already
        # registered (add_middleware, below) before this existed.
        auth_service = AuthService(db, config.auth_providers, config.auth_token_ttl_in_hours)
        app.state.auth_service = auth_service

        # Two independent worker pools, never shared — see jobs/job_queue.py's
        # JobQueue for why a job must never wait on another job from its own
        # queue.
        persisted_job_queue = JobQueue(PersistedJobSink(db), max_concurrent=config.jobs_max_concurrent_persisted)
        ephemeral_job_queue = JobQueue(InMemoryJobSink(), max_concurrent=config.jobs_max_concurrent_ephemeral)

        project_service = ProjectService(db)
        session_manager = ChatSessionManager(db, open_window_minutes=config.max_session_duration_in_minutes)
        
        # A leaf service (see metrics/metric_service.py's own module
        # docstring) — never depends on ChatService/TrackingService, so
        # it's built first and handed to whoever needs it, never the
        # other way around.
        metric_service = MetricService(
            db, project_service, max_session_duration_in_minutes=config.max_session_duration_in_minutes,
        )
        
        # Instantiated once here, not built by ChatService itself (see
        # tracking/tracking_service.py's own module docstring). Both this and
        # ChatService depend on ai_service/metric_service directly, never each other.
        tracking_service = TrackingService(db, ai_service, project_service, metric_service, talk_enabled=talk_service is not None)
        chat_service = ChatService(
            db, ai_service, project_service, session_manager, tracking_service, metric_service, persisted_job_queue,
        )

        # Shares persisted_job_queue/ephemeral_job_queue with anything else
        # that submits a job of either kind (see jobs/job_queue.py's own
        # module docstring) — never its own private queue.
        benchmark_run_service = BenchmarkRunService(
            db, ai_service, tracking_service, persisted_job_queue, ephemeral_job_queue,
        )

        # Availability cascade (see ProjectService.recompute_availability/
        # register_availability_cascade) — same "subscribe once, react
        # forever" shape as WakeupService below.
        project_service.register_availability_cascade()

        chat_ws_adapter = WsAdapter(chat_service, db, auth_service) if config.chat_transport == "websocket" else None

        # Cross-project wake-up (see tracking/wakeup_service.py) —
        # subscribes once for the process lifetime. Built after
        # chat_ws_adapter: a self-loop wake-up needs it to push the
        # transition to an already-open connection.
        WakeupService(db, project_service, ephemeral_job_queue, chat_ws_adapter).register()

        controller = AvanceController(
            chat_service, project_service, talk_service, listen_service, db, tracking_service, benchmark_run_service,
            auth_service, __version__,
        )
        app.include_router(controller.router)

        if chat_ws_adapter is not None:
            adapter = chat_ws_adapter

            @app.websocket("/ws/chat")
            async def chat_ws(websocket: WebSocket) -> None:
                await adapter.chat_loop(websocket)
            
        logger.info("Boot completed - server ready.")

        yield

        # --- SHUTDOWN / CLEANUP ---
        logger.info("Shutting down - cleaning up resources...")
        
        for service in [db, talk_service, listen_service, ai_service]:
            if service is not None and hasattr(service, "close") and callable(getattr(service, "close")):
                close_fn = getattr(service, "close")
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()

    app = FastAPI(title="Avance State Engine", lifespan=lifespan)

    # Registered before CORSMiddleware so CORS ends up the outer layer
    # (Starlette wraps middleware in the reverse of add_middleware() call
    # order) — an early 401 from AuthMiddleware still needs CORS headers
    # attached on its way back out, or the frontend can't even read it.
    app.add_middleware(AuthMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # FIXME: restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    
    return app


try:
    app = create_app()
except Exception as exc:
    logger.exception("Backend failed to start — serving a fallback error app instead of crashing.")
    app = _build_fallback_app(exc)