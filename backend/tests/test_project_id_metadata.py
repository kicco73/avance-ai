"""ProjectService's own half of Prompt 8/9's project.id/ui-label/ui-
description: global uniqueness (AutomatonBuilder can't see what other
projects have already claimed — no database of its own), syncing
Project.project_id/ui_label/ui_description on every successful save
(ProjectsMenu.vue can't afford to parse every project's own index.yml
just to list them), and translating automaton.* reference tokens
(project_id values) into the project_name each one's own declaring
project is actually stored under, for the reverse index.
"""
from __future__ import annotations

import pytest

from db.db import Db

pytestmark = pytest.mark.contract

USERNAME = "user"


def _put(client, name: str, yml: str):
    return client.put(f"/api/projects/{name}", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})


MINIMAL = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def test_project_id_ui_label_ui_description_are_persisted_on_save(client, app_db: Db):
    yml = "project:\n  id: proj_one\n  ui-label: Project One\n  ui-description: The first one.\n" + MINIMAL
    resp = _put(client, "one", yml)
    assert resp.status_code == 200, resp.text

    assert app_db.get_project_id("one") == "proj_one"
    assert app_db.get_project_name_by_project_id("proj_one") == "one"


def test_rejects_a_project_id_already_claimed_by_another_project(client):
    resp = _put(client, "one", "project:\n  id: dup\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "two", "project:\n  id: dup\n" + MINIMAL)

    assert resp.status_code == 400
    assert "dup" in resp.json()["error"]["message"]
    assert "already used by project 'one'" in resp.json()["error"]["message"]


def test_re_saving_the_same_project_with_its_own_existing_id_is_not_a_conflict(client):
    yml = "project:\n  id: stable\n" + MINIMAL
    resp = _put(client, "one", yml)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "one", yml)  # same project, same id — no conflict with itself

    assert resp.status_code == 200, resp.text


def test_changing_a_project_id_frees_up_the_old_one(client):
    resp = _put(client, "one", "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    resp = _put(client, "one", "project:\n  id: new_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text

    # The old id is free again — a different project may now claim it.
    resp = _put(client, "two", "project:\n  id: old_id\n" + MINIMAL)
    assert resp.status_code == 200, resp.text


def test_availability_resolves_automaton_star_against_project_id_not_project_name(client, app_db: Db):
    """The reverse index operates on project_id, never the raw
    project_name (Prompt 8/9) — this project's own on-disk name ("Weird
    Name With Spaces") isn't even a valid identifier, so the only way
    "watcher" can ever resolve its own automaton.dep_id reference is
    through the declared project.id. Checked directly against the
    reverse index itself (db.get_observers), not through an HTTP-
    triggered cascade — the test app fixture doesn't wire ProjectService.
    register_availability_cascade the way main.py does for the real app,
    so there's nothing here to actually propagate an AvailabilityChanged
    event; that cascade mechanism itself is already covered end-to-end,
    project_name-only, in test_project_availability.py."""
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

    assert app_db.get_observers("Weird Name With Spaces") == ["watcher"]
    assert app_db.get_observed_projects("watcher") == ["Weird Name With Spaces"]


def test_a_project_with_no_declared_id_is_never_exposed_to_automaton_star(client):
    resp = _put(client, "watcher", 'project:\n  id: watcher_id\n' + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        "        trigger: \"automaton.silent.state == 'x'\""
    ))
    assert resp.status_code == 200, resp.text
    # "silent" never declares a project.id at all — the reference is
    # simply dangling (never a build-time error, see automaton_builder.py
    # 's own self-loop-only check, which only cares about syntax).
    resp = _put(client, "silent", MINIMAL)
    assert resp.status_code == 200, resp.text

    response = client.get("/api/projects/runtime-status")
    watcher_row = next(r for r in response.json()["projects"] if r["name"] == "watcher")
    assert watcher_row["status"] == "running"  # "silent" isn't paused, so nothing blocks "watcher" either
