from __future__ import annotations

import fcntl
import json
import socket
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from chat.channels import NATIVE_CHAT
from chat.chat_service import ChatService
from chat.ephemeral_env_registry import EphemeralEnvRegistry
from chat.sessions.session_manager import ChatSessionManager
from chat.ws_notifications import WsNotifications
from config import NotificationServiceConfig
from controller import AvanceController
from db import Db
from db.models import User
from error_handlers import ApiErrorHandlers
from events.dispatcher import _reset_for_tests as _reset_dispatcher_for_tests
import bus
from broadcaster import DEFAULT_BATCH_WINDOW_SECONDS, Broadcaster
from jobs.job_queue import JobQueue
from scheduler import SchedulerService
from metrics.metric_service import MetricService
from notification.notification_service import NotificationService
from project.archive.automaton_loader import AutomatonLoader
from project.archive.compiled_automaton_loader import CompiledAutomatonLoader
from project.project_service import ProjectService
from session import Session
from testing.test_service import TestService
from tracking.actuators import TaskNamespaceFactory
from tracking.project_files import PROJECT_FILE_CACHE
from tracking.tracking_service import TrackingService

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"
TEST_STATS_PATH = Path(__file__).resolve().parent.parent / "test_stats.json"
_test_outcomes: dict[str, str] = {}


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


@contextmanager
def chat_socket(client: TestClient, username: str | None = None):
    """The one chat channel a browser has (see chat/ws_notifications.py),
    opened as the current Session().user (or `username`): the User row
    and a real session cookie are minted here, since the `app` fixture
    never goes through AuthMiddleware and the websocket handshake checks
    the cookie itself."""
    app = client.app
    username = username or Session().user
    app.state.db.get_or_create_user("test", f"sub-{username}", username, username, None)
    # A row another path created first (a FK-driven placeholder) may
    # carry no email — verify_token resolves the identity off that column.
    User.update(email=username, role=Session().role).where(User.id == username).execute()
    identity = AuthenticatedUser(provider_user_id=f"sub-{username}", email=username, name=username, picture_url=None)
    token = app.state.auth_service._issue_token(identity, "test")
    with client.websocket_connect("/ws/notifications", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"}) as ws:
        yield ws


def chat_turn_frames(client: TestClient, session_id: int, text: str, turn_id: str = "t1") -> list[dict]:
    """One turn over the websocket, every frame it produced in order —
    the last one is its `done` or `error`."""
    with chat_socket(client) as ws:
        ws.send_json({"type": "input.text", "stream_id": turn_id, "session_id": session_id, "body": text})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("turn.ended", "turn.failed"):
                return frames


def chat_turn(client: TestClient, session_id: int, text: str = "hi") -> dict:
    """The `done` body of one turn, exactly what the browser's own store
    gets (see chatClient.js) — asserts the turn did not fail."""
    final = chat_turn_frames(client, session_id, text)[-1]
    assert final["type"] == "turn.ended", final
    return final


def chat_turn_error(client: TestClient, session_id: int, text: str = "hi") -> dict:
    final = chat_turn_frames(client, session_id, text)[-1]
    assert final["type"] == "error", final
    return final


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    """events.dispatcher's _subscribers dict is a process-global — cleared
    before and after every test so subscriptions never leak between
    tests, regardless of order."""
    _reset_dispatcher_for_tests()
    yield
    _reset_dispatcher_for_tests()


@pytest.fixture(autouse=True)
def _reset_ephemeral_env_registry():
    """EphemeralEnvRegistry is a process-global singleton too — a fresh
    :memory: database (see the `db` fixture below) restarts session ids
    from 1 every time, so without this a test/preview session id from one
    test could collide with an unrelated one's leftover Env from an
    earlier test."""
    EphemeralEnvRegistry._reset_for_tests()
    yield
    EphemeralEnvRegistry._reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_bus():
    """bus's listener registry is a process-global, like events' — a
    decoder registered by one test must not answer another's messages."""
    bus._reset_for_tests()
    yield
    bus._reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_project_file_cache():
    """tracking.project_files' own PROJECT_FILE_CACHE is a process-global
    too, keyed by (project id, revision, path) — and every test starts
    from a fresh :memory: database where those three say nothing about
    which test wrote them. In a real process the same key really does
    mean the same bytes, and ProjectManager.finalize_update invalidates
    the one case where it doesn't."""
    PROJECT_FILE_CACHE.clear()
    yield
    PROJECT_FILE_CACHE.clear()


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

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
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


def make_test_scheduler_service(db: Db, broadcaster=None) -> SchedulerService:
    """A real SchedulerService over a one-worker pool — what every test that
    needs platform jobs (never a TestService's own throttled pool) shares.
    Not started: nothing here claims hibernated tasks unless a test
    calls start() itself (see test_task_defer.py)."""
    return SchedulerService(max_concurrent=1, broadcaster=broadcaster if broadcaster is not None else Broadcaster(), db=db)


def make_test_namespace_factory(
    db: Db, scheduler_service: SchedulerService | None = None, project_service: ProjectService | None = None,
    ai_service=None,
) -> TaskNamespaceFactory:
    """A real TaskNamespaceFactory, wired the same way main.py does — every
    test project's own YAML only ever calls chat.celebrate()/notify(),
    never task.send_mail, so the dummy SMTP config below is never
    actually dialed. Shared by every fixture/helper across the test suite
    that needs to construct a TrackingService/ChatService/WakeupService."""
    scheduler_service = scheduler_service if scheduler_service is not None else make_test_scheduler_service(db)
    project_service = project_service if project_service is not None else ProjectService(db, AutomatonLoader(db), ChatSessionManager(db))
    notification_service = NotificationService(
        NotificationServiceConfig(
            url="smtp://localhost", username="test@example.com", password="", from_name=None, timeout_seconds=5,
        ),
        scheduler_service,
    )
    return TaskNamespaceFactory(notification_service, db, scheduler_service, project_service, ai_service)


@pytest.fixture
def compiled_automata() -> bool:
    """Whether `app` below runs on CompiledAutomatonLoader — what
    `project-service.compiled-automaton` switches in a real deployment. A
    test module that wants the compiled path overrides this fixture with
    one returning True; everything else gets today's loader. The compiled
    loader falls back to the interpreted one whenever no package matches,
    so it is safe from the first request, before anything is built."""
    return False


@pytest.fixture
def app(app_db: Db, fake_ai_service: FakeAiService, tmp_path, compiled_automata: bool) -> FastAPI:
    """The real controller/routing wiring, but against an isolated
    file-backed Db and a FakeAiService, so tests never touch the
    developer's real avance.db or make costly AI calls."""
    automaton_loader = (
        CompiledAutomatonLoader(app_db, tmp_path / "apps") if compiled_automata else AutomatonLoader(app_db)
    )
    project_service = ProjectService(app_db, automaton_loader, ChatSessionManager(app_db), fake_ai_service)
    session_manager = ChatSessionManager(app_db)
    metric_service = MetricService(app_db, project_service)
    test_event_broadcaster = Broadcaster(fake_ai_service, batch_window_seconds=DEFAULT_BATCH_WINDOW_SECONDS)
    scheduler_service = make_test_scheduler_service(app_db, test_event_broadcaster)
    # TestService's own pool, as in main.py — never the platform SchedulerService's.
    test_job_queue = JobQueue(max_concurrent=1, broadcaster=test_event_broadcaster)
    namespace_factory = make_test_namespace_factory(app_db, scheduler_service, project_service, fake_ai_service)
    tracking_service = TrackingService(
        app_db, project_service, metric_service, namespace_factory,
    )
    chat_service = ChatService(
        app_db, fake_ai_service, fake_ai_service, project_service, session_manager,
        tracking_service, metric_service, scheduler_service, namespace_factory,
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
        "build": {"repo-url": None, "username": None, "token": None, "apps-dir": str(tmp_path / "apps")},
    }

    fastapi_app = FastAPI(title="Avance State Engine (test)")
    ApiErrorHandlers.register(fastapi_app)
    controller = AvanceController(
        chat_service, project_service, None, app_db, tracking_service, test_service,
        auth_service, test_event_broadcaster, scheduler_service, "test-version", services_config,
        ws_notifications=WsNotifications(auth_service, chat_service),
        # Never backend/apps: a test that builds must not write into the
        # developer's own working tree.
        apps_dir=tmp_path / "apps",
    )
    fastapi_app.include_router(controller.router)
    fastapi_app.state.test_service = test_service
    fastapi_app.state.project_service = project_service
    fastapi_app.state.chat_service = chat_service
    fastapi_app.state.db = app_db
    fastapi_app.state.auth_service = auth_service
    # For tests that need to watch a task run: start the
    # service and register a fake websocket on the factory (see
    # run_pending_tasks below). Never started here — most tests only
    # ever assert on the Task rows a task leaves behind.
    fastapi_app.state.scheduler_service = scheduler_service
    fastapi_app.state.namespace_factory = namespace_factory
    return fastapi_app


class FakeWebSocket:
    """Just enough to stand in for a WsConnection in WsNotifications'
    username -> connection registry — push only calls send on it."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: dict):
        self.sent.append(payload)


def run_pending_tasks(app: FastAPI, username: str = "user", timeout: float = 5.0) -> list[dict]:
    """Starts the app fixture's SchedulerService (once), attaches a FakeWebSocket
    for `username`, waits until no task is pending or dispatched,
    and returns the frames the browser would have received. Stops the
    service afterwards so its thread never outlives the test."""
    import time
    factory = app.state.namespace_factory
    websocket = FakeWebSocket()
    ws_notifications = WsNotifications(auth_service=None)
    ws_notifications._connections[username] = [websocket]
    scheduler_service = app.state.scheduler_service
    scheduler_service.start()
    try:
        from db.models import Task as TaskRow
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not TaskRow.select().where(TaskRow.status.in_(("pending", "dispatched"))).exists():
                break
            time.sleep(0.02)
    finally:
        scheduler_service.stop()
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


def pytest_runtest_logreport(report) -> None:
    if report.when == "call":
        _test_outcomes[report.nodeid] = "failed" if report.failed else "passed"
    elif report.when in ("setup", "teardown") and report.failed:
        _test_outcomes[report.nodeid] = "failed"
    elif report.when == "setup" and report.skipped and report.nodeid not in _test_outcomes:
        _test_outcomes[report.nodeid] = "skipped"


def _git_renamed_test_files() -> dict[str, str]:
    repo_dir = TEST_STATS_PATH.parent
    try:
        last_commit = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", TEST_STATS_PATH.name],
            cwd=repo_dir, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if not last_commit:
            return {}
        diff = subprocess.run(
            ["git", "diff", "--relative", "--name-status", "-M", "--diff-filter=R", last_commit, "--", "tests/"],
            cwd=repo_dir, capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    renamed = {}
    for line in diff.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            renamed[parts[1]] = parts[2]
    return renamed


def _remap_renamed_files(stats: dict, renamed_files: dict[str, str]) -> dict:
    if not renamed_files:
        return stats
    remapped = {}
    for nodeid, entry in stats.items():
        file_path, sep, rest = nodeid.partition("::")
        remapped[f"{renamed_files.get(file_path, file_path)}{sep}{rest}"] = entry
    return remapped


def _is_full_test_run(session) -> bool:
    """True only for an unfiltered, unrestricted run over the whole
    `testpaths` — the one case where "not in this run's own outcomes"
    unambiguously means "no longer exists" rather than "wasn't selected
    this time". --lf/--ff/--stepwise pass no path/-k/-m of their own
    either, so they need their own exclusion."""
    option = session.config.option
    return (
        session.config.args == ["tests"]
        and not option.keyword
        and not option.markexpr
        and not option.lf
        and not option.failedfirst
        and not option.stepwise
    )


def pytest_sessionfinish(session, exitstatus) -> None:
    if not _test_outcomes:
        return
    TEST_STATS_PATH.touch(exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(TEST_STATS_PATH, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw = f.read()
            stats = _remap_renamed_files(json.loads(raw) if raw else {}, _git_renamed_test_files())
            if _is_full_test_run(session):
                stats = {nodeid: entry for nodeid, entry in stats.items() if nodeid in _test_outcomes}
            for nodeid, outcome in _test_outcomes.items():
                entry = stats.setdefault(
                    nodeid,
                    {"runs": 0, "failures": 0, "skips": 0, "last_outcome": None, "last_run": None, "last_failed": None},
                )
                if outcome == "skipped":
                    entry["skips"] += 1
                else:
                    entry["runs"] += 1
                    entry["failures"] += int(outcome == "failed")
                entry["last_outcome"] = outcome
                entry["last_run"] = now
                if outcome == "failed":
                    entry["last_failed"] = now
            f.seek(0)
            f.truncate()
            json.dump(stats, f, indent=2, sort_keys=True)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
