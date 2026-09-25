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
from turn.input_listener import TurnInput
from turn.turn_service import TurnService
from turn.sessions.session_manager import SessionManager
from automaton.choice_namespace import ChoiceNamespace
from system import bus
from system.bus import POINT_AUTOMATON_LOADER, POINT_CORE_SERVICES, POINT_TRIGGER_NAMESPACES
from system.bus_channel import BusChannel
from system import skills
from config import DEFAULT_ALLOWED_ORIGINS, AppConfig
from tracking.project_files import configure_project_file_cache
from controller import AvanceController
from db import Db
from error_handlers import ApiErrorHandlers
from scheduler import SchedulerService
from system.logging_factory import LoggerFactory
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.archive.loader_choice import AutomatonLoaderChoice
from project.archive.index_yml_migration import modernize_stored_revisions
from project.archive.media_migration import migrate_aspect_archives
from project.archive.packages import discard_all_packages
from project.project_service import ProjectService
from system.project_locks import ProjectLocks
from system.broadcaster import DEFAULT_BATCH_WINDOW_SECONDS, Broadcaster
from tracking.actuators import TaskNamespaceFactory
from tracking.legacy_env_migration import migrate_env_rows
from tracking.tracking_service import TrackingService

__version__ = "2.7.17"

logger = LoggerFactory.get_logger(__name__)


def _build_fallback_app(error: Exception) -> FastAPI:
    """Used only when essential startup wiring fails: every request gets
    the same {error: {message, detail}} shape error_handlers.py produces
    for a normal failure, so the frontend renders it like any other error.
    """
    fallback_app = FastAPI(title="Avance State Engine (misconfigured)")
    fallback_app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEFAULT_ALLOWED_ORIGINS),
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
        logger.info(f"Booting avance headless server v{__version__}.")
        db = Db(config.database_url, migration_strategy=config.database_migration_strategy)
        configure_project_file_cache(config.project_file_cache_bytes)

        migrate_env_rows(db)
        renamed = migrate_aspect_archives(db)

        bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({"db": db}))
        bus.contribute(POINT_TRIGGER_NAMESPACES, lambda namespaces: namespaces.declare(ChoiceNamespace()))
        skills.start_all(config.raw, config.path)
        if renamed | modernize_stored_revisions(db):
            discard_all_packages(config.build_service_config.apps_dir)
        core_services = bus.collect(POINT_CORE_SERVICES, {})
        ai_live_service = core_services.get("ai_live_service")
        ai_test_service = core_services.get("ai_test_service")

        progress_broadcaster = Broadcaster(ai_test_service, batch_window_seconds=DEFAULT_BATCH_WINDOW_SECONDS)
        scheduler_service = SchedulerService(max_concurrent=config.jobs_shared_max_concurrent, broadcaster=progress_broadcaster, db=db)
        app.state.db = db
        session_manager = SessionManager(db, open_window_minutes=config.max_session_duration_in_minutes)
        automaton_loader = bus.collect(POINT_AUTOMATON_LOADER, AutomatonLoaderChoice(
            db=db,
            session_manager=session_manager,
            apps_dir=config.build_service_config.apps_dir,
            loader=AutomatonLoader(db, session_manager=session_manager),
        )).settled()

        project_locks = ProjectLocks()
        project_service = ProjectService(
            db, automaton_loader, session_manager,
            ai_live_service, project_locks=project_locks, 
            invite_valid_days=config.invite_valid_days, invite_max_shares=config.invite_max_shares,
        )
        namespace_factory = TaskNamespaceFactory(db, scheduler_service, project_service, ai_live_service)
        auth_service = AuthService(db, config.auth_providers, config.auth_token_ttl_in_hours, project_service)
        app.state.auth_service = auth_service
        metric_service = MetricService(
            db, project_service, max_session_duration_in_minutes=config.max_session_duration_in_minutes,
        )
        tracking_service = TrackingService(
            db, project_service, metric_service, namespace_factory,
            input_token_budget_per_turn=config.input_token_budget_per_turn,
            total_token_budget_per_session=config.total_token_budget_per_session,
        )
        turn_service = TurnService(
            db, ai_live_service, ai_test_service, project_service, session_manager,
            tracking_service, metric_service, scheduler_service, namespace_factory, project_locks,
            reply_silence_seconds=config.reply_silence_seconds,
        )
        TurnInput(turn_service, db).register()
        progress_broadcaster.bind_loop()
        bus_channel = BusChannel(auth_service)
        bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({
            "auth_service": auth_service,
            "turn_service": turn_service,
            "project_service": project_service,
            "tracking_service": tracking_service,
            "scheduler_service": scheduler_service,
            "namespace_factory": namespace_factory,
            "progress_broadcaster": progress_broadcaster,
            "bus_channel": bus_channel,
            "apps_dir": config.build_service_config.apps_dir,
            "services_config": config.public_services_snapshot(),
            "version": __version__,
        }))
        project_service.register_availability_cascade()
        db.delete_system_warnings_of_kind("project_broken")
        project_service.recompute_all_availability()

        controller = AvanceController(
            turn_service, project_service, bus_channel=bus_channel,
        )
        app.include_router(controller.router)
        scheduler_service.start()

        logger.info("Boot completed - server ready.")

        yield

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
    app.add_middleware(AuthMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
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