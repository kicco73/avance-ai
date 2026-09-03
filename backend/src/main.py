"""FastAPI entrypoint for the Avance State Engine prototype — config/wiring
only. Every endpoint lives on AvanceController (see controller.py)."""

from __future__ import annotations
import inspect
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI
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
from error_handlers import ApiErrorHandlers
from jobs import JobQueue, ThrottledJobQueue
from logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from notification.notification_service import NotificationService
from project.project_service import ProjectService
from ai.ai_service import AiService
from testing.test_service import TestService
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from testing.last_status_broadcaster import LastStatusBroadcaster
from tracking.actuators import ActuatorSetFactory
from tracking.tracking_service import TrackingService
from tracking.wakeup_service import WakeupService
from talk.talk_service import TalkService
from whatsapp.whatsapp_service import WhatsAppService
from listen.listen_service import ListenService

__version__ = "1.27.0"

logger = LoggerFactory.get_logger(__name__)


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
        
        ai_live_service = AiService.for_live(config.ai_services)
        ai_test_service = AiService.for_test(config.ai_services)
        talk_service = TalkService.from_config(config.talk_services) if config.talk_services is not None else None
        listen_service = ListenService.from_config(config.listen_services) if config.listen_services is not None else None
        
        test_event_broadcaster = LastStatusBroadcaster(QueueProgressBroadcaster(ai_test_service))
        job_queue = JobQueue(max_concurrent=config.jobs_shared_max_concurrent, broadcaster=test_event_broadcaster)

        # Always constructed, even with no notification-service section in
        # .config.yml — actuator.send_mail (see tracking/actuators/
        # actuator_set.py) is the only caller, and may never fire; a real
        # attempt to send/enqueue a mail without one configured raises at
        # that point instead of blocking startup for a feature nothing may ever use.
        if config.notification_service_config is None:
            logger.critical("No 'notification-service' section in .config.yml — actuator.send_mail will fail if used.")
        notification_service = NotificationService(config.notification_service_config, job_queue)
        app.state.notification_service = notification_service

        db = Db(config.database_url, migration_strategy=config.database_migration_strategy)
        actuator_factory = ActuatorSetFactory(notification_service, db, job_queue)
        # Bridged onto app.state for the same reason auth_service is below:
        # AuthMiddleware was already registered before this existed, and
        # needs it for its own per-request UserProject ownership check.
        app.state.db = db

        # Built before ProjectService, which injects it into ProjectManager.
        # Also built before AuthService below — AuthService.complete_registration
        # delegates every invite rule (exists/not expired/under its
        # max-shares budget) to ProjectService (see project/invites.py's
        # InviteManager), so it needs this constructed first.
        session_manager = ChatSessionManager(db, open_window_minutes=config.max_session_duration_in_minutes)

        project_service = ProjectService(
            db, ai_live_service,
            invite_valid_days=config.invite_valid_days, invite_max_shares=config.invite_max_shares,
            whatsapp_number=config.whatsapp_service_config.phone_number if config.whatsapp_service_config else None,
            whatsapp_invite_prefix=config.whatsapp_service_config.invite_prefix if config.whatsapp_service_config else "Invitation code: ",
            session_manager=session_manager,
        )

        # Built once here (not a global singleton — see auth/auth_service.py's
        # own module docstring), passed explicitly to whatever needs it.
        # Also bridged onto app.state: AuthMiddleware was already
        # registered (add_middleware, below) before this existed.
        auth_service = AuthService(db, config.auth_providers, config.auth_token_ttl_in_hours, project_service)
        app.state.auth_service = auth_service

        test_job_queue = ThrottledJobQueue(
            max_concurrent=config.test_service_max_concurrent_tests,
            broadcaster=test_event_broadcaster,
            max_jobs_per_minute=config.test_service_max_tests_per_minute,
            min_job_interval_ms=config.test_service_min_test_interval_ms,
        )

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
        tracking_service = TrackingService(
            db, project_service, metric_service, actuator_factory, talk_enabled=talk_service is not None,
            input_token_budget_per_turn=config.input_token_budget_per_turn,
            total_token_budget_per_session=config.total_token_budget_per_session,
        )
        chat_service = ChatService(
            db, ai_live_service, ai_test_service, project_service, session_manager,
            tracking_service, metric_service, job_queue, actuator_factory,
        )

        # Single shared /ws/chat connection per user (see chat/ws_adapter.py) —
        # built after chat_service/auth_service, which it depends on, and
        # handed to whatever else needs to push onto an already-open
        # connection (WakeupService, actuator_factory's own deferred calls).
        ws_adapter = WsAdapter(chat_service, db, auth_service)
        actuator_factory.set_ws_adapter(ws_adapter)

        test_service = TestService(
            db, ai_test_service, tracking_service, test_job_queue, project_service, test_event_broadcaster,
        )

        # Availability cascade (see ProjectService.recompute_availability/
        # register_availability_cascade) — same "subscribe once, react
        # forever" shape as WakeupService below.
        project_service.register_availability_cascade()

        # Cross-project wake-up (see tracking/wakeup_service.py) —
        # subscribes once for the process lifetime.
        WakeupService(
            db, project_service, job_queue, actuator_factory, ws_adapter=ws_adapter, tracking_service=tracking_service,
        ).register()

        # Opt-in (whatsapp-service.enabled in .config.yml): one more
        # client of ChatService.process_turn, beside the SPA — see
        # whatsapp/whatsapp_service.py's own module docstring.
        whatsapp_service = (
            WhatsAppService(
                config.whatsapp_service_config, chat_service, db, auth_service,
                talk_service=talk_service, listen_service=listen_service,
            )
            if config.whatsapp_service_config is not None else None
        )

        controller = AvanceController(
            chat_service, project_service, talk_service, listen_service, db, tracking_service, test_service,
            auth_service, test_event_broadcaster, job_queue, __version__, config.public_services_snapshot(),
            whatsapp_service=whatsapp_service, ws_adapter=ws_adapter,
        )
        app.include_router(controller.router)

        logger.info("Boot completed - server ready.")

        yield

        # --- SHUTDOWN / CLEANUP ---
        logger.info("Shutting down - cleaning up resources...")
        
        for service in [db, talk_service, listen_service, ai_live_service, ai_test_service, whatsapp_service]:
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

    ApiErrorHandlers.register(app)
    
    return app


try:
    app = create_app()
except Exception as exc:
    logger.exception("Backend failed to start — serving a fallback error app instead of crashing.")
    app = _build_fallback_app(exc)