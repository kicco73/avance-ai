from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_service import AuthService
from chat.channels import NATIVE_CHAT
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from config import NotificationServiceConfig
from controller import AvanceController
from db import Db
from error_handlers import ApiErrorHandlers
from events.dispatcher import _reset_for_tests as _reset_dispatcher_for_tests
from job import JobService
from jobs import NullBroadcaster
from jobs.job_queue import JobQueue
from metrics.metric_service import MetricService
from notification.notification_service import NotificationService
from project.project_service import ProjectService
from session import Session
from testing.test_service import TestService
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from testing.last_status_broadcaster import LastStatusBroadcaster
from tracking.actuators import ActuatorSetFactory
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
    assert message is not None and message["queue_status"] == "exited" and message["job_status"] == "completed", response.text
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
    # Session().channel has no per-request middleware in these fixtures
    # (see app()'s own docstring) and Session().impersonate never resets
    # it, so a test that sets it (WhatsApp-channel tests) would otherwise
    # leak WHATSAPP_CHAT into whichever test runs next in this worker.
    Session().channel = NATIVE_CHAT


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

    async def prompt(self, prompt: str, channels: list[str] | None = None) -> str | dict[str, str]:
        text = await self.generate("", [{"role": "user", "content": prompt}])
        if not channels:
            return text
        return {"text": text, **{name: f"Fake {name}." for name in channels}}

    async def generate_stream(self, system_prompt: str, history: list[dict], on_retry=None):
        self.calls.append((system_prompt, history))
        # Must be an actual async generator, not just a coroutine, since
        # callers consume it with `async for`.
        yield "Fake AI reply."

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None):
        # What TurnProtocolUsingSchema actually calls — this fake reports
        # no metadata of its own (no test here cares about signals/audio/
        # env extraction; see FakeSchemaAiService in
        # test_chat_service_evaluation_points.py for that), just the same
        # plain reply text generate_stream above always returned.
        self.calls.append((system_prompt, history))
        yield "Fake AI reply."

    def supports_metadata(self) -> bool:
        return False

    def is_provider_with_schema(self) -> bool:
        return True


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return FakeAiService()


@pytest.fixture
def app_db(tmp_path) -> Db:
    """File-backed, not :memory: — TestClient runs sync endpoints in a real
    threadpool thread, and a second thread's own connection to ":memory:"
    would see a distinct, empty database instead of shared state."""
    return Db(f"sqlite:///{tmp_path / 'test.db'}")


def make_test_job_service(db: Db, broadcaster=None) -> JobService:
    """A real JobService over a one-worker pool — what every test that
    needs platform jobs (never a TestService's own throttled pool) shares.
    Not started: nothing here claims hibernated tasks unless a test
    calls start() itself (see test_actuator_defer_persistence.py)."""
    return JobService(max_concurrent=1, broadcaster=broadcaster if broadcaster is not None else NullBroadcaster(), db=db)


def make_test_actuator_factory(
    db: Db, job_service: JobService | None = None, project_service: ProjectService | None = None,
    ai_service=None,
) -> ActuatorSetFactory:
    """A real ActuatorSetFactory, wired the same way main.py does — every
    test project's own YAML only ever calls actuator.celebrate()/notify(),
    never actuator.send_mail, so the dummy SMTP config below is never
    actually dialed. Shared by every fixture/helper across the test suite
    that needs to construct a TrackingService/ChatService/WakeupService."""
    job_service = job_service if job_service is not None else make_test_job_service(db)
    project_service = project_service if project_service is not None else ProjectService(db)
    notification_service = NotificationService(
        NotificationServiceConfig(
            url="smtp://localhost", username="test@example.com", password="", from_name=None, timeout_seconds=5,
        ),
        job_service,
    )
    return ActuatorSetFactory(notification_service, db, job_service, project_service, ai_service)


@pytest.fixture
def app(app_db: Db, fake_ai_service: FakeAiService) -> FastAPI:
    """The real controller/routing wiring, but against an isolated
    file-backed Db and a FakeAiService, so tests never touch the
    developer's real avance.db or make costly AI calls."""
    project_service = ProjectService(app_db, fake_ai_service)
    session_manager = ChatSessionManager(app_db)
    metric_service = MetricService(app_db, project_service)
    test_event_broadcaster = LastStatusBroadcaster(QueueProgressBroadcaster(fake_ai_service))
    job_service = make_test_job_service(app_db, test_event_broadcaster)
    # TestService's own pool, as in main.py — never the platform JobService's.
    test_job_queue = JobQueue(max_concurrent=1, broadcaster=test_event_broadcaster)
    actuator_factory = make_test_actuator_factory(app_db, job_service, project_service, fake_ai_service)
    tracking_service = TrackingService(
        app_db, project_service, metric_service, actuator_factory,
    )
    chat_service = ChatService(
        app_db, fake_ai_service, fake_ai_service, project_service, session_manager,
        tracking_service, metric_service, job_service, actuator_factory,
    )
    test_service = TestService(
        app_db, fake_ai_service, tracking_service, test_job_queue, project_service, test_event_broadcaster,
    )
    # No real providers: this app fixture never goes through AuthMiddleware
    # (that's only wired in main.py's create_app(), not here) or exercises
    # /api/auth/*, so nothing needs a real Google client id to resolve.
    auth_service = AuthService(app_db, [], token_ttl_in_hours=24 * 7, project_service=project_service)

    # A plausible stand-in for AppConfig.public_services_snapshot() — this
    # fixture never loads a real .config.yml, so the Settings > Manage
    # services page's own read-only payload is faked here instead.
    services_config = {
        "chat": {
            "max-session-duration-in-minutes": 60,
            "input-token-budget-per-turn": 16000,
            "total-token-budget-per-session": 200000,
        },
        "testing": {"max-concurrent-tests": 4, "max-tests-per-minute": 15, "min-test-interval-ms": 0},
        "ai": {
            "max-output-tokens": 1024,
            "providers": [{"driver": "fake", "model": "fake-model", "ui-label": "fake", "ui-description": None, "url": None, "modes": ["live", "test"]}],
        },
        "talk": {"enabled": False, "providers": []},
        "listen": {"enabled": False, "providers": []},
        "database": {"url": "sqlite:///test.db", "migration-strategy": "stop"},
    }

    fastapi_app = FastAPI(title="Avance State Engine (test)")
    ApiErrorHandlers.register(fastapi_app)
    controller = AvanceController(
        chat_service, project_service, None, None, app_db, tracking_service, test_service,
        auth_service, test_event_broadcaster, job_service, "test-version", services_config,
    )
    fastapi_app.include_router(controller.router)
    fastapi_app.state.test_service = test_service
    fastapi_app.state.chat_service = chat_service
    # For tests that need to watch an on-enter task run: start the
    # service and register a fake websocket on the factory (see
    # run_on_enter_tasks below). Never started here — most tests only
    # ever assert on the Task rows an on-enter leaves behind.
    fastapi_app.state.job_service = job_service
    fastapi_app.state.actuator_factory = actuator_factory
    return fastapi_app


class FakeWebSocket:
    """Just enough to stand in for a real connection in WsAdapter's
    username -> WebSocket _connections registry — push only calls
    send_json on it."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict):
        self.sent.append(payload)


def run_on_enter_tasks(app: FastAPI, username: str = "user", timeout: float = 5.0) -> list[dict]:
    """Starts the app fixture's JobService (once), attaches a FakeWebSocket
    for `username`, waits until no on-enter task is pending or dispatched,
    and returns the frames the browser would have received. Stops the
    service afterwards so its thread never outlives the test."""
    import time
    from chat.ws_adapter import WsAdapter

    factory = app.state.actuator_factory
    websocket = FakeWebSocket()
    ws_adapter = WsAdapter(chat_service=app.state.chat_service, db=None, auth_service=None)
    ws_adapter._connections[username] = websocket
    factory.set_ws_adapter(ws_adapter)
    job_service = app.state.job_service
    job_service.start()
    try:
        from db.models import Task as TaskRow
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not TaskRow.select().where(TaskRow.status.in_(("pending", "dispatched"))).exists():
                break
            time.sleep(0.02)
    finally:
        job_service.stop()
    return websocket.sent


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class _FixedSessionMiddleware:
    """Stand-in for the real AuthMiddleware (never wired into the `app`
    fixture, see its own docstring) — sets the same fixed test identity
    `_default_session_user` sets for `client`'s in-process calls, but per
    real request: a `live_server` request runs on uvicorn's own server
    thread, an OS thread `_default_session_user`'s pytest-thread
    assignment never reaches (contextvars don't cross threads on their
    own — see session.py's own docstring)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            Session().user = "user"
            Session().role = "supervisor"
        await self.app(scope, receive, send)


@pytest.fixture
def live_server(app: FastAPI):
    """A real server for the handful of endpoints that only end when the
    client disconnects (SSE) — Starlette's own in-process TestClient runs
    the *whole* ASGI call inside one blocking portal.call() before
    returning anything at all to the caller (see testclient.py's
    _ASGIAdapter.handle_request), so it can never observe a response that
    only completes on client-initiated disconnect: nothing is handed back
    for the client to disconnect *from* yet. A real loopback socket has no
    such deadlock — the server thread and the test thread are genuinely
    concurrent. Serves the exact same `app` object `client` calls hit, so
    it shares every bit of state (Db, job queue, broadcasters) with them."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(_FixedSessionMiddleware(app), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "live_server: uvicorn never reported startup within 5s"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


@pytest.fixture
def hello_project(client: TestClient) -> str:
    """Uploads, activates, and publishes the bundled "Hello world" sample
    project — a project needs a published revision before it can have
    chat sessions. Returns the project's own id (its index.yml declares
    "legacy.hello_world" — put_project.py always uses whatever the
    upload's own project.id says, there's no separate name to request)."""
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.post(
        "/api/projects/upload", content=content, headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.put(f"/api/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id
