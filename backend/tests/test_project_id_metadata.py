"""Tests for project.id (mandatory, globally unique, a plain identifier,
the sole identity a project is known by) and project.family (optional,
free-form text, never parsed — gates automaton.* visibility by plain
equality; None means isolated, neither observing nor observable, itself
included).
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result
from db.db import Db

pytestmark = pytest.mark.contract

MINIMAL = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _upload(client, yml: str):
    return client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})


def _resave(client, project_id: str, yml: str):
    return client.put(
        f"/api/projects/{project_id}/files/index.yml", content=yml.encode(), headers={"Content-Type": "application/x-yaml"}
    )


def _trigger_yml(field_prefix: str, trigger: str) -> str:
    return field_prefix + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        f"        trigger: \"{trigger}\"",
    )


def test_project_id_is_required(client):
    resp = _upload(client, MINIMAL)
    assert resp.status_code == 400
    assert "project.id" in resp.json()["error"]["message"]


def test_project_id_must_be_a_valid_identifier(client):
    resp = _upload(client, "project:\n  id: not-valid\n" + MINIMAL)
    assert resp.status_code == 400
    assert "project.id" in resp.json()["error"]["message"]


def test_project_id_ui_label_ui_description_are_persisted(client, app_db: Db):
    yml = "project:\n  id: proj_one\n  ui-label: Project One\n  ui-description: The first one.\n" + MINIMAL
    resp = _upload(client, yml)
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    assert project_id == "proj_one"
    assert app_db.project_exists("proj_one")


def test_re_uploading_the_same_id_with_no_revision_becomes_a_new_published_revision(client, app_db: Db):
    resp = _upload(client, "project:\n  id: stable\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    assert app_db.get_project_published_revision("stable") == 0

    resp = _upload(client, "project:\n  id: stable\n" + MINIMAL.replace("hi", "hi again"))
    assert resp.status_code == 200, resp.text
    assert app_db.get_project_published_revision("stable") == 1


def test_re_uploading_with_a_revision_not_greater_than_published_is_rejected(client, app_db: Db):
    resp = _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    assert app_db.get_project_published_revision("stable2") == 5

    resp = _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL)
    assert resp.status_code == 400
    assert "not newer" in resp.json()["error"]["message"]


def test_re_uploading_with_a_higher_revision_is_accepted_and_published(client, app_db: Db):
    resp = _upload(client, "project:\n  id: stable3\n  revision: 5\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _upload(client, "project:\n  id: stable3\n  revision: 9\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    assert app_db.get_project_published_revision("stable3") == 9


def test_changing_a_projects_id_through_the_editor_frees_up_the_old_one(client):
    resp = _upload(client, "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _resave(client, "old_id", "project:\n  id: new_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    # The old id is free again — a different project may now claim it.
    resp = _upload(client, "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text


def test_referencing_a_project_with_no_declared_id_anywhere_is_rejected(client):
    """Referencing automaton.<id> where no project anywhere has declared
    that id is a real validation error, not a silently-accepted dangling
    reference."""
    resp = _upload(client, "project:\n  id: silent\n  family: fam\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml("project:\n  id: watcher\n  family: fam\n", "automaton.nonexistent.state == 'x'")
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 400
    assert "automaton.nonexistent" in resp.json()["error"]["message"]


def test_referencing_a_same_family_project_is_accepted(client):
    resp = _upload(client, "project:\n  id: dep\n  family: fam1\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml("project:\n  id: watcher2\n  family: fam1\n", "automaton.dep.state == 'never'")
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 200, resp.text


def test_referencing_a_project_with_no_family_declared_is_rejected_like_unknown(client):
    """"dep_nofam" is a perfectly real, successfully-uploaded project —
    it just never declared a family, so nothing can reference it via
    automaton.*, itself included."""
    resp = _upload(client, "project:\n  id: dep_nofam\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml("project:\n  id: watcher3\n  family: fam2\n", "automaton.dep_nofam.state == 'never'")
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 400
    assert "automaton.dep_nofam" in resp.json()["error"]["message"]


def test_referencing_a_different_family_project_is_rejected_like_unknown(client):
    resp = _upload(client, "project:\n  id: dep_other_fam\n  family: family_a\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml(
        "project:\n  id: watcher4\n  family: family_b\n", "automaton.dep_other_fam.state == 'never'"
    )
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 400
    assert "automaton.dep_other_fam" in resp.json()["error"]["message"]


def test_referencing_an_env_key_not_declared_on_the_named_project_is_rejected(client):
    resp = _upload(client, "project:\n  id: dep_env\n  family: fam3\nenv:\n  known_key:\n    value: \"'x'\"\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml(
        "project:\n  id: watcher5\n  family: fam3\n", "automaton.dep_env.env.missing_key == 1"
    )
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 400
    assert "automaton.dep_env.env.missing_key" in resp.json()["error"]["message"]


def test_referencing_an_env_key_declared_on_the_named_project_is_accepted(client):
    resp = _upload(client, "project:\n  id: dep_env2\n  family: fam4\nenv:\n  known_key:\n    value: \"'x'\"\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    watcher_yml = _trigger_yml(
        "project:\n  id: watcher6\n  family: fam4\n", "automaton.dep_env2.env.known_key == 'x'"
    )
    resp = _upload(client, watcher_yml)
    assert resp.status_code == 200, resp.text
