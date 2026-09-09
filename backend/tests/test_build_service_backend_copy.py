from __future__ import annotations

import sqlite3

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


@pytest.mark.contract
def test_the_installed_skills_are_read_off_the_source_tree():
    """Nothing maintains this list: a package with a skill.py is a skill,
    and its package name is the directory a build either copies or does
    not."""
    import skills

    installed = skills.installed()

    assert {entry["package"] for entry in installed} >= {"listen"}
    listen = next(entry for entry in installed if entry["package"] == "listen")
    assert listen["key"] == "listen" and listen["label"]


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
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import main; import skills; print(skills.installed())"],
        cwd=copy, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "'package': 'listen'" not in result.stdout
