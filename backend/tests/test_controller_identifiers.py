from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.regression

PROJECT = """
project:
  id: identifiers_proj
  family: shared

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


def _upload_and_activate(client, yaml_text: str) -> str:
    """Returns the project's own id — always read off the upload's own
    project.id, mandatory now, there is no name to request separately."""
    response = client.post(
        "/api/projects/upload", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.put(f"/api/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    # get_active_automaton_and_state requires a published revision.
    response = client.post(f"/api/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


def test_returns_one_dict_per_namespace_for_the_active_project(client):
    project_id = _upload_and_activate(client, PROJECT)

    response = client.get(f"/api/projects/{project_id}/identifiers")

    assert response.status_code == 200
    body = response.json()
    # "automaton" is always present, even empty with no other project.
    assert set(body) == {
        "signal", "env", "session", "session.metric", "user", "source", "actuator", "attachment", "metric",
        "automaton", "datetime", "datetime.timezone",
    }
    assert body["automaton"] == {}
    assert body["signal"] == {"myOwnSignal": "whatever this measures"}
    assert body["env"] == {"visits": "How many times this action has fired."}
    assert set(body["session"]) == {
        "current_session_duration_in_minutes", "last_user_session_datetime",
        "number_of_user_sessions", "state_duration_in_minutes",
    }
    assert set(body["session.metric"]) == {"engagement", "state_stability", "signal_stability"}
    assert set(body["user"]) == {
        "email", "name", "picture_url", "provider", "provider_user_id",
        "created_at", "last_login", "active_project", "role", "whatsapp_phone_number",
    }
    assert body["source"] == {}
    assert set(body["metric"]) == {"retention", "activity_consistency"}


def test_404_for_a_project_that_does_not_exist(client):
    response = client.get("/api/projects/does-not-exist/identifiers")

    assert response.status_code == 404


def test_200_for_a_project_that_exists_but_has_never_been_published(client):
    """The identifier registry backs the design view's own autocomplete —
    it must reflect a signal/env key just declared in the draft, even
    before the project's very first publish."""
    response = client.post(
        "/api/projects/upload", content=_zip_of(PROJECT), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]

    response = client.get(f"/api/projects/{project_id}/identifiers")

    assert response.status_code == 200
    assert response.json()["env"] == {"visits": "How many times this action has fired."}


OTHER_PROJECT = """
project:
  id: other_proj
  family: shared

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
    other_id = _upload_and_activate(client, OTHER_PROJECT)
    project_id = _upload_and_activate(client, PROJECT)  # re-activates identifiers_proj

    response = client.get(f"/api/projects/{project_id}/identifiers")

    assert response.status_code == 200
    body = response.json()
    assert body["automaton"] == {}
    assert f"automaton.{project_id}" not in body  # a project never lists itself
    assert body[f"automaton.{other_id}"] == {"state": f"The '{other_id}' project's own current state."}
    assert body[f"automaton.{other_id}.env"] == {"budget": "Remaining shared budget."}


SOURCE_PROJECT = """
project:
  id: source_identifiers_proj

init-action:
  target: a

sources:
  pino:
    ui-label: Flights
    url: avance:behaviour/flights.csv
  unconfigured:
    ui-label: Not set up yet

states:
  a:
    contextual-prompt: "hi"
"""


def _upload_and_activate_with_archive(client, yaml_text: str, archive_name: str, archive_content: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
        zf.writestr(archive_name, archive_content)
    response = client.post(
        "/api/projects/upload", content=buffer.getvalue(), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.put(f"/api/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


def test_source_namespace_lists_one_entry_per_declared_source(client):
    project_id = _upload_and_activate_with_archive(client, SOURCE_PROJECT, "behaviour/flights.csv", "a,b\n1,2\n")

    response = client.get(f"/api/projects/{project_id}/identifiers")

    assert response.status_code == 200
    body = response.json()
    # value(*values, key=...) is a real method for scripts/triggers too
    # (never a model tool — see tracking.sources.SourceDriver.value), so
    # it belongs in the design view's own autocomplete alongside the reads.
    assert set(body["source.pino"]) == {
        "select_rows_containing", "select_rows_where_column", "select_rows_where_column_in_range", "value",
    }
    # A source with no url yet (see AutomatonBuilder._build_source) is
    # still listed under its own name, just with nothing to call on it.
    assert body["source.unconfigured"] == {}
