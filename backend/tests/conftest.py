from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_service import AuthService
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from controller import AvanceController
from db import Db
from error_handlers import register_error_handlers
from events.dispatcher import _reset_for_tests as _reset_dispatcher_for_tests
from jobs import JobQueue
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from session import Session
from testing.test_service import TestService
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from tracking.tracking_service import TrackingService

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def parse_sse_result(response) -> dict:
    """POST .../sessions/import streams its progress as SSE 'data: {...}'
    chunks within the same response, ending with a `completed`/`failed`
    chunk — this picks out that final chunk's `result` payload."""
    message = None
    for line in response.text.strip().split("\n"):
        if line.startswith("data: "):
            message = json.loads(line[len("data: "):])
    assert message is not None and message["status"] == "completed", response.text
    return message["result"]


def _parse_chat_turn_sse_events(response) -> dict:
    events = {}
    for block in response.text.strip().split("\n\n"):
        event_line, data_line = block.split("\n", 1)
        events[event_line[len("event: "):]] = json.loads(data_line[len("data: "):])
    return events


def parse_chat_turn_sse(response) -> dict:
    events = _parse_chat_turn_sse_events(response)
    assert "error" not in events, events.get("error")
    return events["done"]


def parse_chat_turn_sse_error(response) -> dict:
    events = _parse_chat_turn_sse_events(response)
    assert "done" not in events, events.get("done")
    return events["error"]


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    """events.dispatcher's _subscribers dict is a process-global — cleared
    before and after every test so subscriptions never leak between
    tests, regardless of order."""
    _reset_dispatcher_for_tests()
    yield
    _reset_dispatcher_for_tests()


@pytest.fixture(autouse=True)
def _default_session_user():
    Session().user = "user"
    Session().role = "supervisor"


@pytest.fixture
def db() -> Db:
    """A fresh in-memory SQLite database per test — db.py's `database`
    Proxy is a module-level global, so each Db(...) call rebinds it to
    a brand new connection.

    Seeds a User row for "user" — _default_session_user's own default
    Session().user — since EditHistory.user_id/SystemWarning.user_id/
    ChatSession.user/Test.user are now real FKs onto User (see
    models.py): anything writing one of those under the default session
    identity needs a matching row to reference."""
    instance = Db("sqlite:///:memory:")
    instance.get_or_create_user("test", "sub-user", "user", "user", None)
    return instance


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

    def get_total_tokens(self) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096

    def get_input_tokens(self, prompt: str) -> int:
        # Deterministic word-count stand-in — good enough to exercise
        # callers without a real provider's count-tokens call.
        return len(prompt.split())

    async def generate(self, system_prompt: str, history: list[dict], on_retry=None) -> str:
        self.calls.append((system_prompt, history))
        return "Fake AI reply."

    async def generate_stream(self, system_prompt: str, history: list[dict], on_retry=None):
        self.calls.append((system_prompt, history))
        # Must be an actual async generator, not just a coroutine, since
        # callers consume it with `async for`.
        yield "Fake AI reply."

    def supports_metadata(self) -> bool:
        return False

    def is_provider_with_schema(self) -> bool:
        # Routes build_turn_protocol() to the path that calls
        # generate_stream(), the one this fake actually implements.
        return False


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return FakeAiService()


class NullBroadcaster:
    """Stands in for QueueProgressBroadcaster wherever a JobQueue is built but
    the test never exercises SSE — every push() lands here and is dropped,
    since nothing ever connect()s to read it."""

    def push(self, username: str, message: dict) -> None:
        pass


@pytest.fixture
def app_db(tmp_path) -> Db:
    """File-backed, not :memory: — TestClient runs sync endpoints in a real
    threadpool thread, and a second thread's own connection to ":memory:"
    would see a distinct, empty database instead of shared state."""
    return Db(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def app(app_db: Db, fake_ai_service: FakeAiService) -> FastAPI:
    """The real controller/routing wiring, but against an isolated
    file-backed Db and a FakeAiService, so tests never touch the
    developer's real avance.db or make costly AI calls."""
    project_service = ProjectService(app_db, fake_ai_service)
    session_manager = ChatSessionManager(app_db)
    metric_service = MetricService(app_db, project_service)
    test_event_broadcaster = QueueProgressBroadcaster(fake_ai_service)
    job_queue = JobQueue(max_concurrent=1, broadcaster=test_event_broadcaster)
    tracking_service = TrackingService(
        app_db, project_service, metric_service,
    )
    chat_service = ChatService(
        app_db, fake_ai_service, fake_ai_service, project_service, session_manager,
        tracking_service, metric_service, job_queue,
    )
    test_service = TestService(
        app_db, fake_ai_service, tracking_service, job_queue,
    )
    # No real providers: this app fixture never goes through AuthMiddleware
    # (that's only wired in main.py's create_app(), not here) or exercises
    # /api/auth/*, so nothing needs a real Google client id to resolve.
    auth_service = AuthService(app_db, [], token_ttl_in_hours=24 * 7)

    fastapi_app = FastAPI(title="Avance State Engine (test)")
    register_error_handlers(fastapi_app)
    controller = AvanceController(
        chat_service, project_service, None, None, app_db, tracking_service, test_service,
        auth_service, test_event_broadcaster, job_queue, "test-version",
    )
    fastapi_app.include_router(controller.router)
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def hello_project(client: TestClient) -> str:
    """Uploads, activates, and publishes the bundled "Hello world" sample
    project — a project needs a published revision before it can have
    chat sessions."""
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
