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


def _watcher(client, watcher_id: str, family: str, trigger: str):
    yml = f"project:\n  id: {watcher_id}\n  family: {family}\n" + MINIMAL.replace(
        "a:\n    contextual-prompt: hi",
        "a:\n    contextual-prompt: hi\n    actions:\n      - name: notice\n        target: a\n"
        f"        trigger: \"{trigger}\"",
    )
    return _upload(client, yml)


@pytest.mark.parametrize(("yml", "mentions"), [
    (MINIMAL, "project.id"),
    ("project:\n  id: not-valid\n" + MINIMAL, "project.id"),
], ids=["missing-id", "invalid-identifier"])
def test_project_id_is_required_and_must_be_a_valid_identifier(client, yml, mentions):
    resp = _upload(client, yml)
    assert resp.status_code == 400
    assert mentions in resp.json()["error"]["message"]


def test_an_upload_persists_the_declared_project_and_re_uploading_publishes_a_new_revision_only_when_it_is_newer(client, app_db: Db):
    resp = _upload(client, "project:\n  id: proj_one\n  ui-label: Project One\n  ui-description: The first one.\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    assert parse_sse_result(resp)["project_id"] == "proj_one"
    assert app_db.project_exists("proj_one")

    # No declared revision: every re-upload just becomes the next one.
    assert _upload(client, "project:\n  id: stable\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable") == 0
    assert _upload(client, "project:\n  id: stable\n" + MINIMAL.replace("hi", "hi again")).status_code == 200
    assert app_db.get_project_published_revision("stable") == 1

    # A declared revision must actually be greater than the published one.
    assert _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable2") == 5
    resp = _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL)
    assert resp.status_code == 400
    assert "not newer" in resp.json()["error"]["message"]
    assert _upload(client, "project:\n  id: stable2\n  revision: 9\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable2") == 9


def test_changing_a_projects_id_through_the_editor_frees_up_the_old_one(client):
    assert _upload(client, "project:\n  id: old_id\n" + MINIMAL).status_code == 200

    resp = client.put(
        "/api/projects/old_id/files/index.yml",
        content=("project:\n  id: new_id\n" + MINIMAL).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 200, resp.text

    # The old id is free again — a different project may now claim it.
    assert _upload(client, "project:\n  id: old_id\n" + MINIMAL).status_code == 200


@pytest.mark.parametrize(("dependency_yml", "watcher_family", "trigger", "rejected_ref"), [
    ("project:\n  id: silent\n  family: fam\n", "fam", "automaton.nonexistent.state == 'x'", "automaton.nonexistent"),
    ("project:\n  id: dep_nofam\n", "fam2", "automaton.dep_nofam.state == 'never'", "automaton.dep_nofam"),
    ("project:\n  id: dep_other_fam\n  family: family_a\n", "family_b", "automaton.dep_other_fam.state == 'never'", "automaton.dep_other_fam"),
    (
        "project:\n  id: dep_env\n  family: fam3\nenv:\n  known_key:\n    value: \"'x'\"\n",
        "fam3", "automaton.dep_env.env.missing_key == 1", "automaton.dep_env.env.missing_key",
    ),
], ids=["unknown-id", "no-family-declared", "different-family", "unknown-env-key"])
def test_an_automaton_reference_that_does_not_resolve_within_the_same_family_is_rejected(client, dependency_yml, watcher_family, trigger, rejected_ref):
    """A project that never declared a family is a perfectly real,
    successfully-uploaded project — nothing can reference it via
    automaton.*, itself included, exactly like an id nobody declared."""
    assert _upload(client, dependency_yml + MINIMAL).status_code == 200

    resp = _watcher(client, "watcher", watcher_family, trigger)

    assert resp.status_code == 400
    assert rejected_ref in resp.json()["error"]["message"]


def test_a_same_family_projects_state_or_declared_env_key_may_be_referenced(client):
    assert _upload(client, "project:\n  id: dep\n  family: fam1\nenv:\n  known_key:\n    value: \"'x'\"\n" + MINIMAL).status_code == 200

    assert _watcher(client, "watcher_state", "fam1", "automaton.dep.state == 'never'").status_code == 200
    assert _watcher(client, "watcher_env", "fam1", "automaton.dep.env.known_key == 'x'").status_code == 200
