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
from turn.channels import NATIVE_CHAT
from turn.turn_service import TurnService
from turn.ephemeral_env_registry import EphemeralEnvRegistry
from turn.sessions.session_manager import SessionManager
from system.bus_channel import WEB_FORWARDED, BusChannel
from controller import AvanceController
from db import Db
from db.models import User
from error_handlers import ApiErrorHandlers
from events.dispatcher import _reset_for_tests as _reset_dispatcher_for_tests
from system import bus, skills
from system.bus import POINT_CORE_SERVICES
from system.broadcaster import DEFAULT_BATCH_WINDOW_SECONDS, Broadcaster
from scheduler import SchedulerService
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from system.session import Session
from tracking.actuators import TaskNamespaceFactory
from tracking.project_files import PROJECT_FILE_CACHE
from tracking.tracking_service import TrackingService

BACKEND_DIR = Path(__file__).resolve().parent
SRC_ROOT = BACKEND_DIR / "src"
SAMPLES_DIR = BACKEND_DIR / "samples" / "projects"
TEST_STATS_PATH = BACKEND_DIR / "test_stats.json"


def production_sources() -> "list[Path]":
    """Every .py under src/ that is not itself a test. A skill's tests
    live inside its package now (see docs/TESTS.md), so a contract that
    scans the source tree for what production code is allowed to do has
    to say which files it means: a test reaching into an internal on
    purpose is not a boundary being crossed."""
    return [
        path for path in SRC_ROOT.rglob("*.py")
        if "tests" not in path.parts and not path.name.startswith("test_")
    ]


class _TestRun:
    def __init__(self):
        self.outcome = None
        self.seconds = 0.0

    def record(self, report):
        self.seconds += report.duration
        if report.when == "call":
            self.outcome = "failed" if report.failed else "passed"
        elif report.failed:
            self.outcome = "failed"
        elif report.when == "setup" and report.skipped and self.outcome is None:
            self.outcome = "skipped"


_test_runs: dict[str, _TestRun] = {}


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


def installed_skill(package: str) -> None:
    """Abstains rather than fails when the skill a helper needs is not in
    this build. Every core test that reaches a skill's surface goes
    through a helper or a fixture, so this one call is what keeps a
    pruned build's own test run reporting what it left out instead of a
    wall of failures for code that is deliberately absent."""
    if package not in {entry["package"] for entry in skills.installed()}:
        pytest.skip(f"needs {package}, which this build leaves out")


@contextmanager
def chat_socket(client: TestClient, username: str | None = None):
    """The one chat channel a browser has (see system/bus_channel.py),
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
    # A turn typed into the browser is answered by webchat and by nothing
    # else (see webchat/skill.py): without that package the socket opens
    # and no frame ever comes back.
    installed_skill("webchat")
    with client.websocket_connect("/api/core/bus", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"}) as ws:
        # A browser is told nothing it did not register for (see
        # BusChannel._exportable) — this helper stands in for one,
        # so it registers for everything the socket may export.
        ws.send_json({"type": "subscribe", "events": list(WEB_FORWARDED)})
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
    assert final["type"] == "turn.failed", final
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
def _reset_skills():
    """skills' list of started modules is a process-global too, and the
    `app` fixture starts whatever is on disk once per test — without this
    it would grow by one skill per test for the whole run."""
    skills._reset_for_tests()
    yield
    skills._reset_for_tests()


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


def rewrite_archive_content(project_id: str, archive_name: str, revision: int, content: bytes) -> None:
    """Replaces one Archive row's bytes straight through the Db layer's
    own File indirection, keeping its content_type — the direct
    `Archive.update(content=...)` these tests used before the split."""
    from db.models import Archive, File

    row = Archive.get(
        (Archive.project == project_id) & (Archive.archive_name == archive_name) & (Archive.revision == revision)
    )
    content_type = File.get_by_id(row.hash_id).content_type
    Archive.update(hash=File.put(content, content_type)).where(Archive.id == row.id).execute()


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
    interface TurnService actually calls (get_models_info/select_model/
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
        # test_turn_service_evaluation_points.py for that), just the same
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
    needs platform jobs (never a TestingService's own throttled pool) shares.
    Not started: nothing here claims hibernated tasks unless a test
    calls start() itself (see test_task_defer.py)."""
    return SchedulerService(max_concurrent=1, broadcaster=broadcaster if broadcaster is not None else Broadcaster(), db=db)


def make_test_namespace_factory(
    db: Db, scheduler_service: SchedulerService | None = None, project_service: ProjectService | None = None,
    ai_service=None,
) -> TaskNamespaceFactory:
    """A real TaskNamespaceFactory, wired the same way main.py does. Shared
    by every fixture/helper across the test suite that needs to construct
    a TrackingService/TurnService/WakeupService."""
    scheduler_service = scheduler_service if scheduler_service is not None else make_test_scheduler_service(db)
    project_service = project_service if project_service is not None else ProjectService(db, AutomatonLoader(db), SessionManager(db))
    return TaskNamespaceFactory(db, scheduler_service, project_service, ai_service)


@pytest.fixture
def automaton_loader(app_db: Db) -> AutomatonLoader:
    """Which loader `app` reads projects through. The Db/Archive-backed
    one here, as in a bare backend; a package that knows better replaces
    it in a deployment (see bus.POINT_AUTOMATON_LOADER), and the tests
    that belong to that package override this fixture the same way."""
    return AutomatonLoader(app_db)


@pytest.fixture
def raw_config(tmp_path) -> dict:
    """The configuration file as it was read, for the skills `app` starts.
    Every section absent is a skill that registers nothing and says so, so
    what is here is only what a test run needs said differently from the
    defaults. A skill's own tests override this to switch it on."""
    return {"test-service": {"max-concurrent-tests": 1}}


@pytest.fixture
def services_config(tmp_path) -> dict:
    """A plausible stand-in for AppConfig.public_services_snapshot() — this
    fixture never loads a real .config.yml, so the Settings > Manage
    services page's own read-only payload is faked here instead."""
    return {
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


@pytest.fixture
def app(
    app_db: Db, fake_ai_service: FakeAiService, tmp_path,
    automaton_loader: AutomatonLoader, raw_config: dict, services_config: dict,
) -> FastAPI:
    """The real controller/routing wiring, but against an isolated
    file-backed Db and a FakeAiService, so tests never touch the
    developer's real avance.db or make costly AI calls.

    Composed the way main.py composes: the core is built here and offered
    at bus.POINT_CORE_SERVICES, and then *whatever skill is on disk*
    starts against it — nothing here names webchat, testing, the platform
    or the compiler. That is what lets this same harness run inside a
    build that left one of them out: the package is absent, so its routes
    and its tests are absent with it, and no fixture has to be told."""
    project_service = ProjectService(app_db, automaton_loader, SessionManager(app_db), fake_ai_service)
    session_manager = SessionManager(app_db)
    metric_service = MetricService(app_db, project_service)
    progress_broadcaster = Broadcaster(fake_ai_service, batch_window_seconds=DEFAULT_BATCH_WINDOW_SECONDS)
    scheduler_service = make_test_scheduler_service(app_db, progress_broadcaster)
    namespace_factory = make_test_namespace_factory(app_db, scheduler_service, project_service, fake_ai_service)
    tracking_service = TrackingService(
        app_db, project_service, metric_service, namespace_factory,
    )
    turn_service = TurnService(
        app_db, fake_ai_service, fake_ai_service, project_service, session_manager,
        tracking_service, metric_service, scheduler_service, namespace_factory,
    )
    # No real providers: this app fixture never goes through AuthMiddleware
    # (that's only wired in main.py's create_app(), not here) or exercises
    # /api/skills/platform/auth/*, so nothing needs a real Google client id to resolve.
    auth_service = AuthService(app_db, [], token_ttl_in_hours=24 * 7, project_service=project_service)

    fastapi_app = FastAPI(title="Avance State Engine (test)")
    ApiErrorHandlers.register(fastapi_app)
    # One shared connection per identity, as in main.py — the skills that
    # answer a turn collect it from the registry below.
    bus_channel = BusChannel(auth_service)
    bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({
        "db": app_db,
        "auth_service": auth_service,
        "turn_service": turn_service,
        "project_service": project_service,
        "tracking_service": tracking_service,
        "scheduler_service": scheduler_service,
        "ai_test_service": fake_ai_service,
        "progress_broadcaster": progress_broadcaster,
        "bus_channel": bus_channel,
        # Never backend/apps: a test that builds must not write into the
        # developer's own working tree.
        "apps_dir": tmp_path / "apps",
        "services_config": services_config,
        "version": "test-version",
    }))
    skills.start_all(raw_config, Path("."))
    controller = AvanceController(
        turn_service, project_service, bus_channel=bus_channel,
    )
    fastapi_app.include_router(controller.router)
    # Every service the composed system ended up with, under the name it
    # is registered by — including the ones a skill built for itself and
    # offered back (see testing/skill.py). Collected after the router is
    # assembled, which is when a skill's own _install has run.
    for name, service in bus.collect(POINT_CORE_SERVICES, {}).items():
        setattr(fastapi_app.state, name, service)
    # For tests that need to watch a task run: start the
    # service and register a fake websocket on the factory (see
    # run_pending_tasks below). Never started here — most tests only
    # ever assert on the Task rows a task leaves behind.
    fastapi_app.state.namespace_factory = namespace_factory
    return fastapi_app


class FakeWebSocket:
    """Just enough to stand in for a WsConnection in BusChannel'
    username -> connection registry: push only calls send on it, and a
    Bus event only reaches a connection that registered for its type —
    this one stands in for a browser, so it registers for everything the
    socket may export (see BusChannel._exportable)."""

    def __init__(self):
        self.id = "fake-connection"
        self.sent: list[dict] = []

    def wants(self, event_type: str) -> bool:
        return event_type in WEB_FORWARDED

    def send(self, payload: dict):
        self.sent.append(payload)


def run_pending_tasks(app: FastAPI, username: str = "user", timeout: float = 5.0) -> list[dict]:
    """Starts the app fixture's SchedulerService (once), attaches a FakeWebSocket
    for `username`, waits until no task is pending or dispatched,
    and returns the frames the browser would have received. Stops the
    service afterwards so its thread never outlives the test."""
    import time
    websocket = FakeWebSocket()
    bus_channel = BusChannel(auth_service=None)
    bus_channel._connections[username] = [websocket]
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
    # Uploading, activating and publishing are the authoring surface's own
    # routes (see avance_platform/settings_controller.py) — a build without
    # it has no way to put a project there at all.
    installed_skill("avance_platform")
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.post(
        "/api/skills/platform/projects/upload", content=content, headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.post(f"/api/skills/platform/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


def pytest_runtest_logreport(report) -> None:
    _test_runs.setdefault(report.nodeid, _TestRun()).record(report)


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
        session.config.args == list(session.config.getini("testpaths"))
        and not option.keyword
        and not option.markexpr
        and not getattr(option, "lf", False)
        and not getattr(option, "failedfirst", False)
        and not getattr(option, "stepwise", False)
    )


def pytest_collection_modifyitems(config, items):
    if "spawns_a_build" in (config.option.markexpr or ""):
        return
    recursive = [item for item in items if item.get_closest_marker("spawns_a_build")]
    for item in recursive:
        items.remove(item)
    config.hook.pytest_deselected(items=recursive)


def pytest_sessionfinish(session, exitstatus) -> None:
    if not _test_runs:
        return
    TEST_STATS_PATH.touch(exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(TEST_STATS_PATH, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw = f.read()
            stats = _remap_renamed_files(json.loads(raw) if raw else {}, _git_renamed_test_files())
            for entry in stats.values():
                entry.setdefault("first_run", entry.get("last_run") or now)
            full_run = _is_full_test_run(session)
            if full_run:
                stats = {nodeid: entry for nodeid, entry in stats.items() if nodeid in _test_runs}
            for nodeid, run in _test_runs.items():
                outcome = run.outcome
                entry = stats.setdefault(
                    nodeid,
                    {"runs": 0, "failures": 0, "skips": 0, "seconds": 0.0, "first_run": now, "last_outcome": None, "last_run": None, "last_failed": None},
                )
                if outcome == "skipped":
                    entry["skips"] += 1
                else:
                    entry["runs"] += 1
                    entry["failures"] += int(outcome == "failed")
                entry["seconds"] = round(entry.get("seconds", 0.0) + run.seconds, 3)
                if full_run:
                    entry["last_seconds"] = round(run.seconds, 3)
                entry["last_outcome"] = outcome
                entry["last_run"] = now
                if outcome == "failed":
                    entry["last_failed"] = now
            f.seek(0)
            f.truncate()
            json.dump(stats, f, indent=2, sort_keys=True)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
