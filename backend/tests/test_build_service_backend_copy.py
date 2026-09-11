from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from build.build_service import BuildService, _prune_database_to_project
from build.compiler import CompileError
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
    import build.build_service as build_service
    monkeypatch.setattr(build_service, "BUILDS_DIR", tmp_path)

    _publish(db, PROJECT_A)
    db.save_project_files(
        PROJECT_A, {"index.yml": INDEX.format(id=PROJECT_A).replace("hello", "an edit").encode()}, {"index.yml": "text/yaml"},
    )

    with pytest.raises(CompileError, match="publish"):
        BuildService(db, _Service(db), tmp_path / "apps").build_backend_copy(PROJECT_A)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.slow
def test_a_built_backend_copy_is_a_real_launchable_server(tmp_path, monkeypatch):
    import build.build_service as build_service
    build_root = tmp_path / "builds"
    monkeypatch.setattr(build_service, "BUILDS_DIR", build_root)

    db_path = tmp_path / "avance.db"
    source_db = Db(f"sqlite:///{db_path}")
    source_db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(source_db, PROJECT_A)
    revision = source_db.get_project_revision(PROJECT_A)

    result = BuildService(source_db, _Service(source_db), tmp_path / "apps").build_backend_copy(PROJECT_A)

    assert result["revision"] == revision
    backend_copy = build_root / f"{PROJECT_A}.{revision}" / "backend"
    assert str(backend_copy) == result["path"]
    assert (backend_copy / "src" / "main.py").is_file()
    assert (backend_copy / "src" / db_path.name).is_file()
    assert (backend_copy / "apps" / f"{PROJECT_A}.{revision}").is_dir()
    assert not (backend_copy / ".venv").exists()

    connection = sqlite3.connect(str(backend_copy / "src" / db_path.name))
    try:
        assert [row[0] for row in connection.execute("SELECT id FROM Project")] == [PROJECT_A]
    finally:
        connection.close()


@pytest.mark.slow
def test_a_backend_copy_without_talk_still_launches(tmp_path, monkeypatch):
    import build.build_service as build_service
    build_root = tmp_path / "builds"
    monkeypatch.setattr(build_service, "BUILDS_DIR", build_root)

    db_path = tmp_path / "avance.db"
    source_db = Db(f"sqlite:///{db_path}")
    source_db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(source_db, PROJECT_A)
    revision = source_db.get_project_revision(PROJECT_A)

    result = BuildService(source_db, _Service(source_db), tmp_path / "apps").build_backend_copy(
        PROJECT_A, excluded_skills=["talk"],
    )

    backend_copy = build_root / f"{PROJECT_A}.{revision}" / "backend"
    assert str(backend_copy) == result["path"]
    assert not (backend_copy / "src" / "talk").exists()


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

    from build.build_service import BACKEND_DIR, _ignore_for

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


@pytest.mark.contract
@pytest.mark.slow
def test_a_backend_without_listen_still_imports_its_own_entry_point(tmp_path):
    """The reason a build can drop a directory at all: nothing outside it
    names it. If some core file still imported listen, this is where it
    would show."""
    import shutil
    import subprocess
    import sys

    from build.build_service import BACKEND_DIR, _ignore_for

    copy = tmp_path / "backend"
    shutil.copytree(BACKEND_DIR, copy, ignore=_ignore_for(["listen"]))

    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import main; from system import skills; print(skills.installed())"],
        cwd=copy, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "'package': 'listen'" not in result.stdout


@pytest.mark.contract
@pytest.mark.slow
@pytest.mark.parametrize("package", ["talk", "whatsapp"])
def test_a_backend_without_a_skill_still_imports_its_own_entry_point(tmp_path, package):
    """Each of these was threaded through core constructors before it
    became a skill — talk through the studio controller and the tracking
    service, whatsapp through the composition root.
    If any core file still imported one directly rather than reaching it
    through the Bus, this is where it would show."""
    import shutil
    import subprocess
    import sys

    from build.build_service import BACKEND_DIR, _ignore_for

    copy = tmp_path / "backend"
    shutil.copytree(BACKEND_DIR, copy, ignore=_ignore_for([package]))

    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import main; from system import skills; print(skills.installed())"],
        cwd=copy, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert f"'package': '{package}'" not in result.stdout


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

    source = (_Path(__file__).resolve().parent.parent / "src" / "controller.py").read_text()

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

    from build.build_service import BACKEND_DIR, _ignore_for

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
    /api/auth/providers and no /api/state, which is why the build's own
    launch check cannot probe a named route."""
    copy = _copy_backend(tmp_path, ["avance_platform", "build", "webchat", "testing"])
    code, output = _boot(copy)

    assert code == 0, output
    assert FALLBACK_TITLE_MARK not in output, output


def test_every_installed_skill_starts_with_the_configuration_and_nothing_else(tmp_path):
    """A skill's start() takes the configuration, and only that. A core
    object it needs is collected later from POINT_CORE_SERVICES, which
    is what stops this signature from growing a parameter every time one
    skill needs one more thing (see bus.POINT_CORE_SERVICES)."""
    import inspect

    from system import skills

    for module in skills.discover():
        assert list(inspect.signature(module.start).parameters) == ["raw", "path"], module.__name__


def _build_service_for(tmp_path, monkeypatch, build_root):
    """A BuildService whose backend copy is exercised for what it leaves
    on disk, not for whether the copy boots: the launch check needs a
    virtualenv this test has no business building."""
    import build.build_service as build_service
    monkeypatch.setattr(build_service, "BUILDS_DIR", build_root)
    monkeypatch.setattr(BuildService, "_verify_backend_copy_launches", lambda self, backend_copy: None)
    db = Db(f"sqlite:///{tmp_path / 'avance.db'}")
    db.get_or_create_user("test", "sub-user", "user", "user", None)
    _publish(db, PROJECT_A)
    return BuildService(db, _Service(db), tmp_path / "apps"), db


def test_a_rebuild_leaves_no_previous_build_of_the_same_project_behind(tmp_path, monkeypatch):
    """One directory per project, not one per build. Whatever an earlier
    build of this project left in the builds directory is gone — an older
    revision, and the staging directory of a build that died halfway —
    while another project's build is untouched."""
    import build.build_service as build_service
    build_root = tmp_path / "builds"
    build_root.mkdir()
    service, db = _build_service_for(tmp_path, monkeypatch, build_root)
    module = build_service.module_name_for(PROJECT_A)
    revision = db.get_project_revision(PROJECT_A)

    stale = build_root / f"{module}.{revision - 1}"
    stale.mkdir()
    (stale / "marker.txt").write_text("from the build before")
    half_written = build_root / f"{build_service.STAGING_PREFIX}{module}.{revision - 1}"
    half_written.mkdir()
    unrelated = build_root / "some_other_project.1"
    unrelated.mkdir()

    result = service.build_backend_copy(PROJECT_A)

    built = Path(result["path"]).parent
    assert built.is_dir()
    assert not stale.exists()
    assert not half_written.exists()
    assert unrelated.is_dir()
    assert sorted(path.name for path in build_root.iterdir()) == sorted([built.name, unrelated.name])


@pytest.mark.slow
def test_building_the_same_revision_twice_replaces_it(tmp_path, monkeypatch):
    """The same (project, revision) built again is the same directory,
    with nothing of the earlier build left inside it."""
    build_root = tmp_path / "builds"
    build_root.mkdir()
    service, _ = _build_service_for(tmp_path, monkeypatch, build_root)

    first = Path(service.build_backend_copy(PROJECT_A)["path"]).parent
    (first / "left_over.txt").write_text("should not survive")

    second = Path(service.build_backend_copy(PROJECT_A)["path"]).parent

    assert second == first
    assert not (second / "left_over.txt").exists()
    assert [path.name for path in build_root.iterdir()] == [second.name]
