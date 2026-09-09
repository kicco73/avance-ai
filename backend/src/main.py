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
from turn.turn_service import TurnService
from turn.sessions.session_manager import SessionManager
from system import bus
from system.bus import OUTPUT_SPEECH, POINT_CORE_SERVICES
from system.ws_notifications import WsNotifications
from system import skills
from config import AppConfig
from tracking.project_files import configure_project_file_cache
from controller import AvanceController
from db import Db
from error_handlers import ApiErrorHandlers
from scheduler import SchedulerService
from jobs.throttled_job_queue import ThrottledJobQueue
from system.logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.archive.compiled_automaton_loader import CompiledAutomatonLoader
from project.health_notifications import ProjectHealthNotifications
from project.project_service import ProjectService
from ai import AiService
from testing.test_service import TestService
from system.broadcaster import DEFAULT_BATCH_WINDOW_SECONDS, Broadcaster
from tracking.actuators import TaskNamespaceFactory
from tracking.legacy_env_migration import migrate_env_rows
from tracking.tracking_service import TrackingService
from tracking.wakeup_service import WakeupService

__version__ = "1.33.0"

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

        # Built before AiService.for_live/for_test below, so both cascades
        # can be given it directly — Manage services' own daily token
        # usage (see AiService.generate_stream_with_metadata's on_metadata
        # tap and db/ai_usage.py) is written straight through it, the same
        # `db` every other service here depends on.
        db = Db(config.database_url, migration_strategy=config.database_migration_strategy)

        # Process-wide, and so configured here rather than handed to each
        # of the readers that share it (see tracking.project_files).
        configure_project_file_cache(config.project_file_cache_bytes)

        migrate_env_rows(db)

        ai_live_service = AiService.for_live(
            config.ai_services, db=db, input_token_budget_per_turn=config.input_token_budget_per_turn,
        )
        ai_test_service = AiService.for_test(
            config.ai_services, db=db, input_token_budget_per_turn=config.input_token_budget_per_turn,
        )

        test_event_broadcaster = Broadcaster(ai_test_service, batch_window_seconds=DEFAULT_BATCH_WINDOW_SECONDS)
        # Started last (see the end of this block): until then its Task
        # table only gains rows, nothing is claimed.
        scheduler_service = SchedulerService(max_concurrent=config.jobs_shared_max_concurrent, broadcaster=test_event_broadcaster, db=db)

        # Whatever is installed, started with the configuration file as it
        # was read: nothing here names a skill, and a build that leaves a
        # package out simply has one fewer (see skills.py).
        skills.start_all(config.raw, config.path)

        # Bridged onto app.state for the same reason auth_service is below:
        # AuthMiddleware was already registered before this existed, and
        # needs it for its own per-request UserProject ownership check.
        app.state.db = db

        # Built before ProjectService, which injects it into ProjectManager.
        # Also built before AuthService below — AuthService.complete_registration
        # delegates every invite rule (exists/not expired/under its
        # max-shares budget) to ProjectService (see project/invites.py's
        # InviteManager), so it needs this constructed first.
        session_manager = SessionManager(db, open_window_minutes=config.max_session_duration_in_minutes)

        # XXX Compiled automaton requirement - do not touch.
        # XXX The one place the compiled/interpreted choice is made (see
        # config.py's project-service.compiled-automaton). On/off and
        # nothing more: which package answers for which project and
        # revision is decided per load, against build-service.apps-dir.
        automaton_loader = (
            CompiledAutomatonLoader(
                db, config.build_service_config.apps_dir, session_manager=session_manager,
            )
            if config.use_compiled_automata
            else AutomatonLoader(db, session_manager=session_manager)
        )

        project_service = ProjectService(
            db, automaton_loader, session_manager,
            ai_live_service, 
            invite_valid_days=config.invite_valid_days, invite_max_shares=config.invite_max_shares,
        )

        # After ProjectService (a hibernated task.defer is rebuilt
        # against a project revision through it) and before the SchedulerService
        # is started: this registers the task type the scheduler hydrates.
        namespace_factory = TaskNamespaceFactory(db, scheduler_service, project_service, ai_live_service)

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
        # docstring) — never depends on TurnService/TrackingService, so
        # it's built first and handed to whoever needs it, never the
        # other way around.
        metric_service = MetricService(
            db, project_service, max_session_duration_in_minutes=config.max_session_duration_in_minutes,
        )
        
        # Instantiated once here, not built by TurnService itself (see
        # tracking/tracking_service.py's own module docstring). Both this and
        # TurnService depend on ai_service/metric_service directly, never each other.
        tracking_service = TrackingService(
            db, project_service, metric_service, namespace_factory,
            talk_enabled=bool(bus.handlers_for(OUTPUT_SPEECH)),
            input_token_budget_per_turn=config.input_token_budget_per_turn,
            total_token_budget_per_session=config.total_token_budget_per_session,
        )
        turn_service = TurnService(
            db, ai_live_service, ai_test_service, project_service, session_manager,
            tracking_service, metric_service, scheduler_service, namespace_factory,
        )

        # A flush runs on a job-worker thread, and a listener that ends
        # up writing to a socket needs this loop rather than that one.
        test_event_broadcaster.bind_loop()

        # One shared connection per identity, and not the chat's: the
        # whole SPA reads it (see system/__init__.py). It subscribes to
        # the Bus's ui.* messages in its own constructor, and publishes
        # what a client sends without knowing who — if anyone — answers.
        ws_notifications = WsNotifications(auth_service)

        # The composed core, offered to whoever asks for it. Everything a
        # skill could need exists by now; nothing is handed to anyone,
        # and a skill that is not in this build asks for nothing (see
        # bus.POINT_CORE_SERVICES, system/skills.py). Contributed after
        # ws_notifications rather than before, so the registry is
        # readable at the moment it is built and not only later.
        bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({
            "db": db,
            "auth_service": auth_service,
            "turn_service": turn_service,
            "project_service": project_service,
            "tracking_service": tracking_service,
            "scheduler_service": scheduler_service,
            "ws_notifications": ws_notifications,
        }))

        test_service = TestService(
            db, ai_test_service, tracking_service, test_job_queue, project_service, test_event_broadcaster,
        )

        # Availability cascade (see ProjectService.recompute_availability/
        # register_availability_cascade) — same "subscribe once, react
        # forever" shape as WakeupService below.
        project_service.register_availability_cascade()

        # Admin-facing side effect of a published revision going broken/
        # healthy again (see project/health_notifications.py) — registered
        # before the boot-time sweep below, so a project already broken
        # when this process starts is logged/warned/pushed exactly once.
        ProjectHealthNotifications(db, scheduler_service).register()

        # Every project's own build health (published/draft) is unknown
        # to this fresh process until checked — a framework change since
        # the last boot may have broken one silently; this is what turns
        # that into a paused project plus a single admin notification
        # instead of a 500 on whichever endpoint happens to touch it first.
        project_service.recompute_all_availability()

        # Cross-project wake-up (see tracking/wakeup_service.py) —
        # subscribes once for the process lifetime.
        WakeupService(
            db, project_service, scheduler_service, namespace_factory, tracking_service=tracking_service,
            ai_service=ai_live_service,
        ).register()

        controller = AvanceController(
            turn_service, project_service, db, tracking_service, test_service,
            auth_service, test_event_broadcaster, scheduler_service, __version__, config.public_services_snapshot(),
            ws_notifications=ws_notifications,
            apps_dir=config.build_service_config.apps_dir,
        )
        app.include_router(controller.router)

        # Last: everything a due task may reach (the websocket adapter
        # above all) now exists, and every task type is registered.
        scheduler_service.start()

        logger.info("Boot completed - server ready.")

        yield

        # --- SHUTDOWN / CLEANUP ---
        logger.info("Shutting down - cleaning up resources...")
        
        await skills.stop_all()
        for service in [db, ai_live_service, ai_test_service]:
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