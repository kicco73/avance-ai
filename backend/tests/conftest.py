from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from session import Session
from tracking.tracking_service import TrackingService

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


@pytest.fixture
def db() -> Db:
    """A fresh in-memory SQLite database per test — db.py's `database`
    Proxy is a module-level global, so each Db(...) call simply rebinds it
    to a brand new connection (fine for the sequential tests here)."""
    return Db("sqlite:///:memory:")


class FakeAiService:
    """Stands in for ai.ai_service.AiService in integration tests — same
    interface ChatService actually calls (get_models_info/select_model/
    generate/generate_stream), but never touches a real provider: no
    network calls, no cost, no flakiness, deterministic replies."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    async def generate(self, system_prompt: str, history: list[dict], on_retry=None) -> str:
        self.calls.append((system_prompt, history))
        return "Fake AI reply."

    async def generate_stream(self, system_prompt: str, history: list[dict], on_retry=None):
        self.calls.append((system_prompt, history))
        # Must actually be an async generator (an `async def` with no
        # `yield` is just a coroutine, not iterable via `async for` —
        # see tracking/turn_protocol_using_text_extraction.py's own
        # `async for chunk in self._ai_service.generate_stream(...)`).
        yield "Fake AI reply."

    def supports_metadata(self) -> bool:
        return False

    def is_provider_with_schema(self) -> bool:
        # Routes TrackingProcessor.build_turn_protocol() to
        # TurnProcotolUsingTextExtraction (see tracking/tracking_processor.py),
        # which only calls generate_stream() — the one this fake actually
        # implements.
        return False


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return FakeAiService()


@pytest.fixture
def app_db(tmp_path) -> Db:
    """File-backed, not :memory: — TestClient runs sync endpoints in a
    real threadpool thread (see FastAPI's run_in_threadpool), and a
    :memory: SQLite database is private to the single connection that
    created it: a second thread opening its own connection to ":memory:"
    gets a distinct, empty database instead of sharing state, which
    surfaces as "no such table" the moment a request hits a sync route."""
    return Db(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def app(app_db: Db, fake_ai_service: FakeAiService) -> FastAPI:
    """The real controller/routing wiring (see main.py), but against an
    isolated file-backed Db and a FakeAiService instead of main.py's real
    AppConfig-driven ones — so these tests never touch the developer's
    actual avance.db file (a previous incident: repeatedly recreating
    that shared file from test scripts corrupted a concurrently running
    dev server's connection to it) nor make real, costly AI calls."""
    project_service = ProjectService(app_db)
    session_manager = ChatSessionManager(app_db)
    metric_service = MetricService(
        app_db,
        get_username=lambda: Session().user,
        get_active_project_name=lambda: project_service.get_active_project_name(),
    )
    tracking_service = TrackingService(
        app_db, fake_ai_service, project_service, metric_service,
    )
    chat_service = ChatService(
        app_db, fake_ai_service, project_service, session_manager, tracking_service, metric_service
    )

    fastapi_app = FastAPI(title="Avance State Engine (test)")
    register_error_handlers(fastapi_app)
    controller = AvanceController(chat_service, project_service, None, None, app_db, tracking_service)
    fastapi_app.include_router(controller.router)
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def hello_project(client: TestClient) -> str:
    """Uploads, activates, and publishes the bundled "Hello world" sample
    project — for tests that need a real active project/automaton, not
    just an empty one. Published because a project with no published
    revision yet can't have chat sessions (see Db.create_chat_session)."""
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.put(
        "/api/projects/hello", content=content, headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    response = client.put("/api/projects/hello/activate")
    assert response.status_code == 200, response.text
    response = client.post("/api/projects/hello/publish", json={})
    assert response.status_code == 200, response.text
    return "hello"
