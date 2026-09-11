"""A whole product, compiled, answering a real chat turn.

Every other test of this work checks one seam at a time: the compiler
against the builder, the loader's decision, a package's own attachments.
None of them shows the thing they are for — a project that is uploaded,
published, built, and then actually serves, with the platform reading a
compiled package instead of Archive rows and nothing downstream aware of
the difference.

The loader is on from the first request, before anything is built. That
is the point: it falls back to the interpreted automaton whenever no
package matches, so the same deployment serves a project before and after
its first build without any switch being flipped in between. Publishing
compiles on its own where this package is installed (see skill.py's
POINT_PROJECT_PUBLISHED contribution), so a test that wants the
before state removes the package these fixtures already produced.
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from automaton.automaton import CompiledAutomaton
from db import Db
from project.archive.packages import package_dir
from build.build_service import module_name_for
from build.compiled_automaton_loader import CompiledAutomatonLoader

from conftest import chat_turn


@pytest.fixture
def automaton_loader(app_db: Db, tmp_path) -> CompiledAutomatonLoader:
    return CompiledAutomatonLoader(app_db, tmp_path / "apps")


def _discard_package(app, tmp_path, project_id: str) -> None:
    revision = app.state.db.get_project_published_revision(project_id)
    shutil.rmtree(package_dir(tmp_path / "apps", module_name_for(project_id), revision))


def _served_automaton(app, project_id: str):
    db = app.state.db
    loader = app.state.project_service.automaton_loader
    return loader.load_at_revision(project_id, db.get_project_published_revision(project_id))


def test_a_project_serves_interpreted_until_it_is_built_and_compiled_after(
    client: TestClient, app, hello_project, tmp_path,
):
    _discard_package(app, tmp_path, hello_project)
    assert type(_served_automaton(app, hello_project)).__name__ == "Automaton"
    before = chat_turn(client, client.get("/api/skills/webchat/sessions/current").json()["id"], "hello")
    assert before["reply"][0]["content"]
    assert before["state"]["key"] == "Hello"

    response = client.post(f"/api/skills/build/projects/{hello_project}/local-module")
    assert response.status_code == 200, response.text
    built = response.json()
    assert built["path"] == str(
        package_dir(tmp_path / "apps", module_name_for(hello_project), built["revision"])
    )

    assert isinstance(_served_automaton(app, hello_project), CompiledAutomaton)
    after = chat_turn(client, client.get("/api/skills/webchat/sessions/current").json()["id"], "hello")
    assert after["reply"][0]["content"] == before["reply"][0]["content"]
    assert after["state"] == before["state"], "the same turn, the same answer, from a package"


def test_the_app_store_listing_reports_whether_the_project_is_served_compiled(
    client: TestClient, app, hello_project, tmp_path,
):
    def _compiled_flag() -> bool:
        apps = client.get("/api/skills/platform/app-store/apps").json()["apps"]
        return next(a for a in apps if a["id"] == hello_project)["compiled"]

    _discard_package(app, tmp_path, hello_project)
    assert _compiled_flag() is False

    assert client.post(f"/api/skills/build/projects/{hello_project}/local-module").status_code == 200

    assert _compiled_flag() is True


def test_the_design_view_reads_a_compiled_project_like_any_other(client: TestClient, app, hello_project):
    """The panel is the part most likely to notice a different automaton:
    it asks for payloads, a graph, signals and per-state token estimates,
    none of which a chat turn goes near."""
    graph_before = client.get(f"/api/skills/platform/projects/{hello_project}/graph").json()
    signals_before = client.get(f"/api/skills/platform/projects/{hello_project}/signals").json()

    assert client.post(f"/api/skills/build/projects/{hello_project}/local-module").status_code == 200
    assert isinstance(_served_automaton(app, hello_project), CompiledAutomaton)

    assert client.get(f"/api/skills/platform/projects/{hello_project}/graph").json() == graph_before
    assert client.get(f"/api/skills/platform/projects/{hello_project}/signals").json() == signals_before
    assert client.get(f"/api/skills/platform/projects/{hello_project}").status_code == 200


def test_editing_the_project_again_takes_it_back_to_the_interpreted_automaton(
    client: TestClient, app, hello_project,
):
    """A draft is never served compiled — the package belongs to the
    published revision, and an edit creates a revision that has none."""
    assert client.post(f"/api/skills/build/projects/{hello_project}/local-module").status_code == 200
    assert isinstance(_served_automaton(app, hello_project), CompiledAutomaton)

    db = app.state.db
    published = db.get_project_published_revision(hello_project)
    index = db.get_archive(hello_project, "index.yml", revision=published).decode()
    response = client.put(
        f"/api/skills/platform/projects/{hello_project}/files/index.yml",
        content=index.replace("hello, world!", "hola, mundo!").encode(),
        headers={"Content-Type": "text/yaml"},
    )
    assert response.status_code == 200, response.text
    draft = db.get_project_revision(hello_project)
    assert draft != published

    loader = app.state.project_service.automaton_loader
    assert type(loader.load_at_revision(hello_project, draft)).__name__ == "Automaton"
    assert isinstance(loader.load_at_revision(hello_project, published), CompiledAutomaton)


def test_a_project_with_unpublished_changes_cannot_be_built_through_the_panel(client: TestClient, app, hello_project):
    db = app.state.db
    published = db.get_project_published_revision(hello_project)
    index = db.get_archive(hello_project, "index.yml", revision=published).decode()
    client.put(
        f"/api/skills/platform/projects/{hello_project}/files/index.yml",
        content=index.replace("hello, world!", "hola, mundo!").encode(),
        headers={"Content-Type": "text/yaml"},
    )

    response = client.post(f"/api/skills/build/projects/{hello_project}/local-module")

    assert response.status_code == 400
    assert "publish" in str(response.json())
