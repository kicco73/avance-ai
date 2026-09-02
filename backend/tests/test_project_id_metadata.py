"""Tests for project.id/ui-label/ui-description: global uniqueness,
syncing Project.project_id/ui_label/ui_description on every successful
save, and translating automaton.* project_id tokens into project_name
for the reverse index.
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result
from db.db import Db

pytestmark = pytest.mark.contract

USERNAME = "user"


def _put(client, name: str, yml: str):
    return client.put(f"/api/projects/{name}", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})


def _resave(client, name: str, yml: str):
    return client.put(f"/api/projects/{name}/files/index.yml", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})


MINIMAL = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def test_project_id_ui_label_ui_description_are_persisted_on_save(client, app_db: Db):
    yml = "project:\n  id: proj_one\n  ui-label: Project One\n  ui-description: The first one.\n" + MINIMAL
    resp = _put(client, "one", yml)
    assert resp.status_code == 200, resp.text
    project_name = parse_sse_result(resp)["project_name"]

    assert app_db.get_project_id(project_name) == "proj_one"
    assert app_db.get_project_name_by_project_id("proj_one") == project_name


def test_rejects_a_project_id_already_claimed_by_another_project(client):
    resp = _put(client, "one", "project:\n  id: dup\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    first_name = parse_sse_result(resp)["project_name"]

    resp = _put(client, "two", "project:\n  id: dup\n" + MINIMAL)

    assert resp.status_code == 400
    assert "dup" in resp.json()["error"]["message"]
    assert f"already used by project '{first_name}'" in resp.json()["error"]["message"]


def test_re_saving_the_same_project_with_its_own_existing_id_is_not_a_conflict(client):
    yml = "project:\n  id: stable\n" + MINIMAL
    resp = _put(client, "one", yml)
    assert resp.status_code == 200, resp.text
    project_name = parse_sse_result(resp)["project_name"]

    resp = _resave(client, project_name, yml)  # same project, same id — no conflict with itself

    assert resp.status_code == 200, resp.text


def test_changing_a_project_id_frees_up_the_old_one(client):
    resp = _put(client, "one", "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    project_name = parse_sse_result(resp)["project_name"]

    resp = _resave(client, project_name, "project:\n  id: new_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    # The old id is free again — a different project may now claim it.
    resp = _put(client, "two", "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text


def test_availability_resolves_automaton_star_against_project_id_not_project_name(client, app_db: Db):
    """The reverse index is stored keyed by project_id, never the raw
    project_name — this project's on-disk name ("Weird Name With
    Spaces") isn't a valid identifier, so "watcher" can only resolve its
    automaton.dep_id reference through the declared project.id, and the
    index itself records "dep_id", not "Weird Name With Spaces"."""
    resp = _put(
        client, "Weird Name With Spaces",
        "project:\n  id: dep_id\n" + MINIMAL,
    )
    assert resp.status_code == 200, resp.text

    watcher_yml = (
        "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
        "    actions:\n      - name: notice\n        target: a\n"
        "        trigger: \"automaton.dep_id.state == 'never'\"\n"
    )
    resp = _put(client, "watcher", watcher_yml)
    assert resp.status_code == 200, resp.text

    assert app_db.get_observers("dep_id") == ["watcher"]
    assert app_db.get_observed_projects("watcher") == ["dep_id"]


def test_referencing_a_project_with_no_declared_id_anywhere_is_rejected(client):
    """Referencing automaton.<id> where no project anywhere has declared
    that id is a real validation error, not a silently-accepted dangling
    reference."""
    resp = _put(client, "silent", MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "watcher", 'project:\n  id: watcher_id\n' + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        "        trigger: \"automaton.silent.state == 'x'\""
    ))
    assert resp.status_code == 400
    assert "automaton.silent" in resp.json()["error"]["message"]


def test_referencing_an_env_key_not_declared_on_the_named_project_is_rejected(client):
    resp = _put(client, "dep", "project:\n  id: dep_id\nenv:\n  known_key:\n    value: \"'x'\"\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "watcher", 'project:\n  id: watcher_id\n' + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        "        trigger: \"automaton.dep_id.env.missing_key == 1\""
    ))
    assert resp.status_code == 400
    assert "automaton.dep_id.env.missing_key" in resp.json()["error"]["message"]


def test_referencing_an_env_key_declared_on_the_named_project_is_accepted(client):
    resp = _put(client, "dep", "project:\n  id: dep_id\nenv:\n  known_key:\n    value: \"'x'\"\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "watcher", 'project:\n  id: watcher_id\n' + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        "        trigger: \"automaton.dep_id.env.known_key == 'x'\""
    ))
    assert resp.status_code == 200, resp.text
