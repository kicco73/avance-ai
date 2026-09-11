"""GET /api/projects/{project_id}/signals — see ProjectService.get_project_signals.
Each signal's `relevant` field feeds the Inspector's "show only relevant
signals" filter directly. Scoped to `state_key`'s outgoing actions when
given, else falls back to every state's triggers combined."""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.regression

PROJECT = """
init-action:
  target: a

signals:
  progress:
    definition: "How far along the exercise the user is, 0-100."
  score:
    definition: "The user's own score for this exercise, 0-100."

states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "signal.progress == 100"
  b:
    contextual-prompt: "bye"
"""

# Two states, each with a trigger referencing a *different* signal — the
# minimum shape needed to prove state_key actually changes which signal
# comes back `relevant`, not just whether the endpoint accepts the param.
TWO_STATE_PROJECT = """
init-action:
  target: a

signals:
  progressSignal:
    definition: "Relevant only to state a's own trigger."
  moodSignal:
    definition: "Relevant only to state b's own trigger."
  unusedSignal:
    definition: "Never referenced anywhere."

states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "signal.progressSignal == 100"
  b:
    contextual-prompt: "middle"
    actions:
      - name: finish
        ui-label: Finish
        target: c
        trigger: "signal.moodSignal >= 50"
  c:
    contextual-prompt: "bye"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload(client, project_id: str, yaml_text: str) -> str:
    response = client.post(
        "/api/projects/upload",
        content=_zip_of(f"project:\n  id: {project_id}\n" + yaml_text),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    result = parse_sse_result(response)
    assert result["project_id"] == project_id
    return result["project_id"]


def test_signals_report_whether_a_trigger_references_them(client):
    project_id = _upload(client, "relevance_test", PROJECT)

    response = client.get(f"/api/projects/{project_id}/signals")

    assert response.status_code == 200
    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["progress"]["relevant"] is True
    assert by_name["score"]["relevant"] is False


def test_a_signal_referenced_only_via_env_field_is_also_relevant(client):
    project = PROJECT.replace(
        'trigger: "signal.progress == 100"',
        'trigger: "signal.progress == 100"\n        env:\n          last_score: signal.score',
    ).replace(
        "states:",
        "env:\n  last_score: {}\n\nstates:",
    )
    project_id = _upload(client, "relevance_env_test", project)

    response = client.get(f"/api/projects/{project_id}/signals")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["score"]["relevant"] is True


def test_without_state_key_relevance_is_every_states_triggers_combined(client):
    project_id = _upload(client, "two_state_test", TWO_STATE_PROJECT)

    response = client.get(f"/api/projects/{project_id}/signals")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["progressSignal"]["relevant"] is True
    assert by_name["moodSignal"]["relevant"] is True
    assert by_name["unusedSignal"]["relevant"] is False


def test_state_key_scopes_relevance_to_that_states_own_outgoing_triggers(client):
    project_id = _upload(client, "two_state_scoped_test", TWO_STATE_PROJECT)

    response_a = client.get(f"/api/projects/{project_id}/signals?state_key=a")
    by_name_a = {s["signal"]["name"]: s for s in response_a.json()["signals"]}
    assert by_name_a["progressSignal"]["relevant"] is True
    assert by_name_a["moodSignal"]["relevant"] is False

    response_b = client.get(f"/api/projects/{project_id}/signals?state_key=b")
    by_name_b = {s["signal"]["name"]: s for s in response_b.json()["signals"]}
    assert by_name_b["progressSignal"]["relevant"] is False
    assert by_name_b["moodSignal"]["relevant"] is True


def test_an_unknown_state_key_falls_back_to_every_states_triggers_combined(client):
    project_id = _upload(client, "unknown_state_key_test", TWO_STATE_PROJECT)

    response = client.get(f"/api/projects/{project_id}/signals?state_key=not-a-real-state")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["progressSignal"]["relevant"] is True
    assert by_name["moodSignal"]["relevant"] is True
