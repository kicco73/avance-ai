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
    # get_active_automaton_and_state (see ProjectService) now requires a
    # published revision — a draft-only project can no longer resolve an
    # active automaton at all outside EditProject's own dedicated draft
    # entry points.
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text


def test_returns_one_dict_per_namespace_for_the_active_project(client):
    _upload_and_activate(client, "identifiers-proj", PROJECT)

    response = client.get("/api/chat/identifiers")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"signal", "env", "system", "session", "session.metric", "metric"}
    assert body["signal"] == {"myOwnSignal": "whatever this measures"}
    # Sourced from the project's own declared env: section (parallel to
    # signals:), not left empty (see automaton.identifier_registry.
    # build_registry).
    assert body["env"] == {"visits": "How many times this action has fired."}
    assert set(body["system"]) == {"today", "time"}
    assert set(body["session"]) == {
        "current_session_duration_in_minutes", "last_user_session_datetime",
        "number_of_user_sessions", "state_duration_in_minutes",
    }
    assert set(body["session.metric"]) == {"engagement", "state_stability", "signal_stability"}
    assert set(body["metric"]) == {"retention", "activity_consistency"}


def test_404_when_no_project_is_active(client):
    response = client.get("/api/chat/identifiers")

    assert response.status_code == 404
