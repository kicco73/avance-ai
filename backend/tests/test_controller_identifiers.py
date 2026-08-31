from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.regression

PROJECT = """
init-action:
  target: a

signals:
  myOwnSignal:
    ui-description: "whatever this measures"
    definition: "whatever"

env:
  visits:
    ui-description: "How many times this action has fired."

states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "signal.myOwnSignal >= 1"
        env:
          visits: "1"
  b:
    contextual-prompt: "bye"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload_and_activate(client, name: str, yaml_text: str):
    response = client.put(
        f"/api/projects/{name}", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text
    # get_active_automaton_and_state requires a published revision.
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text


def test_returns_one_dict_per_namespace_for_the_active_project(client):
    _upload_and_activate(client, "identifiers-proj", PROJECT)

    response = client.get("/api/projects/identifiers-proj/identifiers")

    assert response.status_code == 200
    body = response.json()
    # "automaton" is always present, even empty with no other project.
    assert set(body) == {"signal", "env", "system", "session", "session.metric", "user", "metric", "automaton"}
    assert body["automaton"] == {}
    assert body["signal"] == {"myOwnSignal": "whatever this measures"}
    assert body["env"] == {"visits": "How many times this action has fired."}
    assert set(body["system"]) == {"today", "time"}
    assert set(body["session"]) == {
        "current_session_duration_in_minutes", "last_user_session_datetime",
        "number_of_user_sessions", "state_duration_in_minutes",
    }
    assert set(body["session.metric"]) == {"engagement", "state_stability", "signal_stability"}
    assert set(body["user"]) == {
        "email", "name", "picture_url", "provider", "provider_user_id",
        "created_at", "last_login", "active_project", "role",
    }
    assert set(body["metric"]) == {"retention", "activity_consistency"}


def test_404_for_a_project_that_does_not_exist(client):
    response = client.get("/api/projects/does-not-exist/identifiers")

    assert response.status_code == 404


def test_200_for_a_project_that_exists_but_has_never_been_published(client):
    """The identifier registry backs the design view's own autocomplete —
    it must reflect a signal/env key just declared in the draft, even
    before the project's very first publish."""
    response = client.put(
        "/api/projects/draft-only-proj", content=_zip_of(PROJECT), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text

    response = client.get("/api/projects/draft-only-proj/identifiers")

    assert response.status_code == 200
    assert response.json()["env"] == {"visits": "How many times this action has fired."}


OTHER_PROJECT = """
project:
  id: other_proj

init-action:
  target: x

env:
  budget:
    ui-description: "Remaining shared budget."

states:
  x:
    contextual-prompt: "hi"
"""


def test_automaton_namespace_lists_every_other_project_never_the_active_one(client):
    _upload_and_activate(client, "other-proj", OTHER_PROJECT)
    _upload_and_activate(client, "identifiers-proj", PROJECT)  # re-activates identifiers-proj

    response = client.get("/api/projects/identifiers-proj/identifiers")

    assert response.status_code == 200
    body = response.json()
    assert body["automaton"] == {}
    assert "automaton.identifiers-proj" not in body  # never declared a project.id at all
    assert body["automaton.other_proj"] == {"state": "The 'other-proj' project's own current state."}
    assert body["automaton.other_proj.env"] == {"budget": "Remaining shared budget."}
