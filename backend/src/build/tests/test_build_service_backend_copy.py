from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import build.backend_copy as backend_copy
from build.backend_copy import STEPS, _prune_database_to_project
from build.build_service import BuildService, module_name_for
from build.compiler import CompileError
from conftest import parse_sse_result
from db import Db

PROJECT_A = "keep_me"
PROJECT_B = "drop_me"
INDEX = """
project:
  id: {id}
init-action:
  target: start
general-prompt: hello
states:
  start:
    contextual-prompt: go
"""


class _Service:
    def __init__(self, db: Db) -> None:
        self._db = db

    def get_published_revision(self, project_id: str) -> int:
        revision = self._db.get_project_published_revision(project_id)
        if revision is None:
            raise ValueError(f"Project '{project_id}' has never been published.")
        return revision


async def _build_artifacts(service: BuildService, project_id: str, excluded_skills: "list[str] | None" = None) -> dict:
    """Every step but the last. What a build leaves on disk is what these
    tests are about; running the built backend's own suite is minutes and
    has its own test below."""
    return await _run(service.backend_copy_job(project_id, excluded_skills), len(STEPS) - 1)


async def _build_and_test(service: BuildService, project_id: str, excluded_skills: "list[str] | None" = None) -> dict:
    """The whole job, test run included — what the Build view triggers."""
    return await _run(service.backend_copy_job(project_id, excluded_skills), len(STEPS))


async def _run(job, steps: int) -> dict:
    job.prepare()
    for _ in range(steps):
        await job.run_next_step()
    return json.loads(job.result)


def _publish(db: Db, project_id: str) -> None:
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": INDEX.format(id=project_id).encode()}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)


def test_pruning_drops_every_other_project_and_what_belongs_to_it(db, tmp_path):
    _publish(db, PROJECT_A)
    _publish(db, PROJECT_B)
    session_a = db.create_chat_session("alice", PROJECT_A, db.get_project_revision(PROJECT_A))
    session_b = db.create_chat_session("bob", PROJECT_B, db.get_project_revision(PROJECT_B))
    db.save_message("user", "hi from A", session_a)
    db.save_message("user", "hi from B", session_b)

    copy_path = tmp_path / "copy.db"
    copy_path.write_bytes(db.export_backup())
    _prune_database_to_project(copy_path, PROJECT_A)

    connection = sqlite3.connect(str(copy_path))
    try:
        project_ids = {row[0] for row in connection.execute("SELECT id FROM Project")}
        assert project_ids == {PROJECT_A}
        session_projects = {row[0] for row in connection.execute("SELECT project_id FROM ChatSession")}
        assert session_projects == {PROJECT_A}
        message_count = connection.execute("SELECT COUNT(*) FROM Message").fetchone()[0]
        assert message_count == 1
    finally:
        connection.close()


def test_pruning_a_database_with_only_the_kept_project_is_a_no_op(db, tmp_path):
    _publish(db, PROJECT_A)
    copy_path = tmp_path / "copy.db"
    copy_path.write_bytes(db.export_backup())

    _prune_database_to_project(copy_path, PROJECT_A)

    connection = sqlite3.connect(str(copy_path))
    try:
        assert [row[0] for row in connection.execute("SELECT id FROM Project")] == [PROJECT_A]
    finally:
        connection.close()


def test_a_project_with_unpublished_changes_cannot_be_built(db, tmp_path, monkeypatch):
    """Refused where the request can still see it — before there is a job
    at all, so the Build view gets a 400 rather than a stream that starts
    and stops."""
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", tmp_path)

    _publish(db, PROJECT_A)
    db.save_project_files(
        PROJECT_A, {"index.yml": INDEX.format(id=PROJECT_A).replace("hello", "an edit").encode()}, {"index.yml": "text/yaml"},
    )

    with pytest.raises(CompileError, match="publish"):
        BuildService(db, _Service(db), tmp_path / "apps").backend_copy_job(PROJECT_A)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.slow
async def test_a_built_backend_copy_is_a_whole_backend_around_one_project(tmp_path, monkeypatch):
    build_root = tmp_path / "builds"
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", build_root)

    db_path = tmp_path / "avance.db"
    source_db = Db(f"sqlite:///{db_path}")
    source_db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(source_db, PROJECT_A)
    revision = source_db.get_project_revision(PROJECT_A)

    result = await _build_artifacts(BuildService(source_db, _Service(source_db), tmp_path / "apps"), PROJECT_A)

    assert result["revision"] == revision
    built = build_root / f"{PROJECT_A}.{revision}" / "backend"
    assert str(built) == result["path"]
    assert (built / "src" / "main.py").is_file()
    assert (built / "src" / db_path.name).is_file()
    assert (built / "apps" / f"{PROJECT_A}.{revision}").is_dir()
    assert not (built / ".venv").exists()
    # The tests travel with the code they test — that is what the build's
    # own last step runs (see build/backend_copy.py).
    assert (built / "tests" / "test_wiring_contract.py").is_file()
    assert (built / "conftest.py").is_file()
    assert (built / "pytest.ini").is_file()

    connection = sqlite3.connect(str(built / "src" / db_path.name))
    try:
        assert [row[0] for row in connection.execute("SELECT id FROM Project")] == [PROJECT_A]
    finally:
        connection.close()


@pytest.mark.slow
async def test_a_skill_left_out_takes_its_own_tests_with_it(tmp_path, monkeypatch):
    """The whole reason a skill's tests live inside its package: there is
    no list of which tests belong to what, and nothing to keep in sync —
    the directory that is not copied is not there to be collected."""
    build_root = tmp_path / "builds"
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", build_root)

    db_path = tmp_path / "avance.db"
    source_db = Db(f"sqlite:///{db_path}")
    source_db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(source_db, PROJECT_A)
    revision = source_db.get_project_revision(PROJECT_A)

    result = await _build_artifacts(
        BuildService(source_db, _Service(source_db), tmp_path / "apps"), PROJECT_A, ["talk"],
    )

    built = build_root / f"{PROJECT_A}.{revision}" / "backend"
    assert str(built) == result["path"]
    assert not (built / "src" / "talk").exists()
    assert (built / "src" / "whatsapp" / "tests").is_dir()


@pytest.mark.contract
def test_the_installed_skills_are_read_off_the_source_tree():
    """Nothing maintains this list: a package with a skill.py is a skill,
    and its package name is the directory a build either copies or does
    not."""
    from system import skills

    installed = skills.installed()

    assert {entry["package"] for entry in installed} >= {"listen"}
    listen = next(entry for entry in installed if entry["package"] == "listen")
    assert listen["key"] == "listen" and listen["ui_label"] and listen["ui_description"]


@pytest.mark.contract
def test_a_skill_left_out_of_a_build_is_a_directory_that_is_not_copied(tmp_path):
    """The switch is the absence of the code, not a flag inside it —
    there is nothing in the built copy that records the choice, and
    nothing at run time that could read it back."""
    import shutil

    from build.backend_copy import BACKEND_DIR, _ignore_for

    full = tmp_path / "full"
    without = tmp_path / "without"
    shutil.copytree(BACKEND_DIR, full, ignore=_ignore_for([]))
    shutil.copytree(BACKEND_DIR, without, ignore=_ignore_for(["listen"]))

    assert (full / "src" / "listen" / "skill.py").is_file()
    assert not (without / "src" / "listen").exists()
    # Only that one directory: excluding a skill must not take anything
    # else with it.
    assert (
        {path.name for path in (full / "src").iterdir()} - {path.name for path in (without / "src").iterdir()}
        == {"listen"}
    )


def test_webchat_is_offered_as_something_a_build_can_leave_out(tmp_path):
    """What the Build view's Skills step lists is read off the source
    tree, so this is the whole of "add it to the UI": the package has a
    skill.py, therefore it has a checkbox."""
    from system import skills

    assert "webchat" in {skill["package"] for skill in skills.installed()}


def test_the_composition_root_names_no_controller_a_build_could_leave_out(tmp_path):
    """AvanceController builds one controller — the per-project views,
    which every build answers. Everything else arrives through
    bus.POINT_HTTP_CONTROLLERS, so a package that is not in the build
    contributes nothing and its routes are simply not there."""
    from pathlib import Path as _Path

    source = (_Path(__file__).resolve().parents[2] / "controller.py").read_text()

    assert "ProjectController" in source
    for left_out in ("EditProjectController", "SettingsController", "AuthController",
                     "BuildController", "AppStoreController", "LabelProjectController",
                     "UserController", "PlatformController"):
        assert left_out not in source, left_out


BOOTABLE_CONFIG = """
database:
  url: "sqlite:///boot-check.db"

turn-service: {}

ai-service:
  providers:
    - driver: gemini
      model: gemini-flash-lite-latest
      key: fake-key

auth-service:
  providers:
    - driver: google
      key: fake-client-id

mail-service:
  url: "smtp://smtp.example.com:587"
  username: fake-username
  password: fake-password
"""

# What main.py titles the app it serves when create_app() raised. Importing
# main succeeds either way — that is the whole point of the fallback, and
# the reason "import main works" proved nothing about whether the backend
# actually starts.
FALLBACK_TITLE_MARK = "misconfigured"


def _boot(copy: "Path") -> "tuple[int, str]":
    """Starts the copied backend's app object in a fresh interpreter and
    reports the title it ended up with. Not a subprocess server and no
    port: the question is only whether create_app() got to the end."""
    import subprocess
    import sys

    (copy / "src" / ".config.yml").write_text(BOOTABLE_CONFIG)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'src'); import main; print('TITLE:' + main.app.title)"],
        cwd=copy, capture_output=True, text=True, timeout=300,
    )
    return result.returncode, result.stdout + result.stderr


def _copy_backend(tmp_path: "Path", excluded: "list[str]") -> "Path":
    import shutil

    from build.backend_copy import BACKEND_DIR, _ignore_for

    copy = tmp_path / "backend"
    shutil.copytree(BACKEND_DIR, copy, ignore=_ignore_for(excluded))
    return copy


@pytest.mark.slow
def test_a_full_backend_copy_starts_for_real_and_not_as_the_fallback_app(tmp_path):
    """The check `import main` alone never made: main.py catches a failed
    create_app() and serves a fallback app that answers 503 to
    everything, so the import succeeds no matter how broken startup is.
    Every skill is in this copy, including product/ — which is in every
    build until somebody unticks it."""
    code, output = _boot(_copy_backend(tmp_path, []))

    assert code == 0, output
    assert "TITLE:" in output, output
    assert FALLBACK_TITLE_MARK not in output, output


@pytest.mark.slow
def test_a_product_copy_starts_with_no_platform_no_chat_and_no_benchmark(tmp_path):
    """The shape the whole exercise is for: one compiled project, a
    channel, and nothing to author with — and no compiler either, since
    a product serves a package somebody else built. It has no
    /api/skills/platform/auth/providers and no /api/skills/platform/state, which is why the build's own
    launch check cannot probe a named route."""
    copy = _copy_backend(tmp_path, ["avance_platform", "build", "webchat", "testing"])
    code, output = _boot(copy)

    assert code == 0, output
    assert FALLBACK_TITLE_MARK not in output, output


@pytest.mark.contract
@pytest.mark.slow
def test_a_backend_without_listen_talk_or_whatsapp_starts_for_real(tmp_path):
    code, output = _boot(_copy_backend(tmp_path, ["listen", "talk", "whatsapp"]))

    assert code == 0, output
    assert "TITLE:" in output, output
    assert FALLBACK_TITLE_MARK not in output, output


def test_every_installed_skill_starts_with_the_configuration_and_nothing_else(tmp_path):
    """A skill's start() takes the configuration, and only that. A core
    object it needs is collected later from POINT_CORE_SERVICES, which
    is what stops this signature from growing a parameter every time one
    skill needs one more thing (see bus.POINT_CORE_SERVICES)."""
    import inspect

    from system import skills

    for skill in skills.discover():
        assert list(inspect.signature(skill.start).parameters) == ["raw", "path"], skill.package


def _build_service_for(tmp_path, monkeypatch, build_root):
    """A BuildService whose backend copy is exercised for what it leaves
    on disk, not for whether the copy boots: the launch check needs a
    virtualenv this test has no business building."""
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", build_root)
    db = Db(f"sqlite:///{tmp_path / 'avance.db'}")
    db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(db, PROJECT_A)
    return BuildService(db, _Service(db), tmp_path / "apps"), db


async def test_a_rebuild_leaves_no_previous_build_of_the_same_project_behind(tmp_path, monkeypatch):
    """One directory per project, not one per build. Whatever an earlier
    build of this project left in the builds directory is gone — an older
    revision, and the staging directory of a build that died halfway —
    while another project's build is untouched."""
    build_root = tmp_path / "builds"
    build_root.mkdir()
    service, db = _build_service_for(tmp_path, monkeypatch, build_root)
    module = module_name_for(PROJECT_A)
    revision = db.get_project_revision(PROJECT_A)

    stale = build_root / f"{module}.{revision - 1}"
    stale.mkdir()
    (stale / "marker.txt").write_text("from the build before")
    half_written = build_root / f"{backend_copy.STAGING_PREFIX}{module}.{revision - 1}"
    half_written.mkdir()
    unrelated = build_root / "some_other_project.1"
    unrelated.mkdir()

    result = await _build_artifacts(service, PROJECT_A)

    built = Path(result["path"]).parent
    assert built.is_dir()
    assert not stale.exists()
    assert not half_written.exists()
    assert unrelated.is_dir()
    assert sorted(path.name for path in build_root.iterdir()) == sorted([built.name, unrelated.name])


@pytest.mark.slow
async def test_building_the_same_revision_twice_replaces_it(tmp_path, monkeypatch):
    """The same (project, revision) built again is the same directory,
    with nothing of the earlier build left inside it."""
    build_root = tmp_path / "builds"
    build_root.mkdir()
    service, _ = _build_service_for(tmp_path, monkeypatch, build_root)

    first = Path((await _build_artifacts(service, PROJECT_A))["path"]).parent
    (first / "left_over.txt").write_text("should not survive")

    second = Path((await _build_artifacts(service, PROJECT_A))["path"]).parent

    assert second == first
    assert not (second / "left_over.txt").exists()
    assert [path.name for path in build_root.iterdir()] == [second.name]


@pytest.mark.slow
@pytest.mark.spawns_a_build
async def test_a_build_ends_by_running_its_own_suite_for_real(tmp_path, monkeypatch):
    """The whole thing, no fakes: a backend is built and then its own
    pytest run has to pass before the build does. Minutes, and it builds
    a backend that would otherwise build a backend — which is what the
    marker is for (see pytest.ini, build/backend_copy.py)."""
    build_root = tmp_path / "builds"
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", build_root)
    db_path = tmp_path / "avance.db"
    source_db = Db(f"sqlite:///{db_path}")
    source_db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(source_db, PROJECT_A)

    result = await _build_and_test(BuildService(source_db, _Service(source_db), tmp_path / "apps"), PROJECT_A)

    assert result["tests"]["passed"], result["tests"]["output"]
    assert "passed" in result["tests"]["summary"]


@pytest.mark.contract
def test_the_last_step_of_a_build_is_running_the_built_backends_own_tests():
    assert [step.key for step in STEPS][-1] == "tests"
    assert STEPS[-1].label == "Running the build's tests"


async def test_the_test_step_runs_pytest_inside_the_build_and_never_recursively(tmp_path, monkeypatch):
    """Against the built directory, not this one — and without the tests
    that build a backend themselves, which would build a backend that
    builds a backend."""
    copy = backend_copy.BackendCopy(None, PROJECT_A, 3, PROJECT_A, [])
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", tmp_path)
    recorded = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(command, 0, stdout="12 passed in 3.4s\n", stderr="")

    monkeypatch.setattr(backend_copy.subprocess, "run", fake_run)

    copy.run_tests()

    assert recorded["cwd"] == tmp_path / f"{PROJECT_A}.3" / "backend"
    assert recorded["command"][1:3] == ["-m", "pytest"]
    assert recorded["command"][-2:] == ["-m", "not spawns_a_build"]
    assert copy.report()["tests"] == {
        "passed": True, "summary": "12 passed in 3.4s", "output": "12 passed in 3.4s",
    }


async def test_a_build_whose_own_tests_fail_is_a_failed_build(tmp_path, monkeypatch):
    """The build stays on disk — it was published before the tests ran —
    and the job says why it failed, so the panel shows the failure rather
    than a green build nobody looked at."""
    copy = backend_copy.BackendCopy(None, PROJECT_A, 3, PROJECT_A, [])
    monkeypatch.setattr(backend_copy, "BUILDS_DIR", tmp_path)
    monkeypatch.setattr(
        backend_copy.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="1 failed, 2 passed\n", stderr=""),
    )

    with pytest.raises(CompileError, match="tests failed"):
        copy.run_tests()

    assert copy.report()["tests"]["passed"] is False


def test_the_route_streams_the_build_instead_of_waiting_for_it(client, hello_project, monkeypatch):
    """A backend copy is minutes, so the response is the job's own
    progress and the last chunk carries the report (see
    SchedulerService.stream_progress). Driven here over one trivial step:
    what is being checked is the wiring, not the copying."""
    import build.build_job as build_job

    ran = []
    monkeypatch.setattr(
        build_job, "STEPS", (backend_copy.BuildStep("only", "The only step", "report"),),
    )
    monkeypatch.setattr(backend_copy.BackendCopy, "report", lambda self: ran.append(self.project_id) or {"path": "x"})

    response = client.post(f"/api/skills/build/projects/{hello_project}/backend-copy", json={"excluded_skills": []})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert parse_sse_result(response)["steps"] == [
        {"key": "only", "label": "The only step", "status": "done"},
    ]
    assert ran and ran[0] == hello_project


def test_a_project_that_cannot_be_built_is_refused_before_any_job_exists(client, hello_project, app):
    """The 400 the panel shows verbatim, still a 400 now that the build
    itself streams: nothing has started when this answers."""
    app.state.db.save_project_files(
        hello_project, {"index.yml": b"project:\n  id: hello_world\n"}, {"index.yml": "text/yaml"},
    )

    response = client.post(f"/api/skills/build/projects/{hello_project}/backend-copy", json={"excluded_skills": []})

    assert response.status_code == 400
    assert "publish" in response.json()["error"]["message"]
