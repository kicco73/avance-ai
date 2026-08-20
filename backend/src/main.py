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
from session import Session
from tracking.tracking_service import TrackingService
from tracking.wakeup_service import WakeupService
from talk.talk_service import TalkService
from listen.listen_service import ListenService

__version__ = "1.3.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


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

        # Two independent worker pools, never shared — see jobs/job_queue.py's
        # JobQueue for why a job must never wait on another job from its own
        # queue. Neither has a consumer yet: no job kind has been moved onto
        # this engine yet, this just wires the generic mechanism up.
        persisted_job_queue = JobQueue(PersistedJobSink(db), max_concurrent=config.jobs_max_concurrent_persisted)
        ephemeral_job_queue = JobQueue(InMemoryJobSink(), max_concurrent=config.jobs_max_concurrent_ephemeral)

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
        tracking_service = TrackingService(db, ai_service, project_service, metric_service)
        chat_service = ChatService(
            db, ai_service, project_service, session_manager, tracking_service, metric_service, persisted_job_queue,
        )

        # Shares persisted_job_queue/ephemeral_job_queue with anything else
        # that submits a job of either kind (see jobs/job_queue.py's own
        # module docstring) — never its own private queue.
        benchmark_run_service = BenchmarkRunService(
            db, ai_service, tracking_service, persisted_job_queue, ephemeral_job_queue,
        )

        # Availability cascade (Prompt 7, see ProjectService.
        # recompute_availability/register_availability_cascade's own
        # docstrings) — same "subscribe once, react forever" shape as
        # WakeupService below.
        project_service.register_availability_cascade()

        chat_ws_adapter = WsAdapter(chat_service, db) if config.chat_transport == "websocket" else None

        # Cross-project wake-up (see tracking/wakeup_service.py's own
        # module docstring) — subscribes at startup, for the whole
        # process's lifetime; nothing else ever calls into this directly,
        # it only ever reacts to events TrackingEngine publishes.
        # Constructed after chat_ws_adapter (Prompt 12, unlike before this
        # parameter existed): a self-loop wake-up needs it to push the
        # transition to an already-open connection (see WakeupService.
        # _reevaluate_and_apply) — None whenever config.chat_transport
        # isn't 'websocket' at all, in which case push is simply skipped.
        WakeupService(db, project_service, ephemeral_job_queue, chat_ws_adapter).register()

        controller = AvanceController(
            chat_service, project_service, talk_service, listen_service, db, tracking_service, benchmark_run_service,
        )
        app.include_router(controller.router)

        if chat_ws_adapter is not None:
            adapter = chat_ws_adapter

            @app.websocket("/ws/chat")
            async def chat_ws(websocket: WebSocket) -> None:
                await adapter.chat_loop(websocket)
            
        logger.info("Boot completed - server ready.")

        # L'applicazione FastAPI rimane attiva qui durante la gestione delle richieste
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