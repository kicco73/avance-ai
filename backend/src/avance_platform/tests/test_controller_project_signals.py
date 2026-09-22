"""GET /api/core/projects/{project_id}/signals — see ProjectService.get_project_signals.
Each signal's `relevant` field feeds the Inspector's "show only relevant
signals" filter directly. Scoped to what a turn in `state_key` computes
(its `signal-tracking-strategy`) when given, else falls back to every state's
combined."""
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
    input-processor: ai
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "signal.progress == 100"
  b:
    input-processor: ai
    contextual-prompt: "bye"
"""
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
    input-processor: ai
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "signal.progressSignal == 100"
  b:
    input-processor: ai
    contextual-prompt: "middle"
    actions:
      - name: finish
        ui-label: Finish
        target: c
        trigger: "signal.moodSignal >= 50"
  c:
    input-processor: ai
    contextual-prompt: "bye"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload(client, project_id: str, yaml_text: str) -> str:
    response = client.post(
        "/api/skills/platform/projects/upload",
        content=_zip_of(f"project:\n  id: {project_id}\n" + yaml_text),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    result = parse_sse_result(response)
    assert result["project_id"] == project_id
    return result["project_id"]


def test_signals_report_whether_a_trigger_references_them(client):
    project_id = _upload(client, "relevance_test", PROJECT)

    response = client.get(f"/api/core/projects/{project_id}/signals")

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
        "env:\n  last_score:\n    type: number\n\nstates:",
    )
    project_id = _upload(client, "relevance_env_test", project)

    response = client.get(f"/api/core/projects/{project_id}/signals")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["score"]["relevant"] is True


def test_without_state_key_relevance_is_every_states_triggers_combined(client):
    project_id = _upload(client, "two_state_test", TWO_STATE_PROJECT)

    response = client.get(f"/api/core/projects/{project_id}/signals")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["progressSignal"]["relevant"] is True
    assert by_name["moodSignal"]["relevant"] is True
    assert by_name["unusedSignal"]["relevant"] is False


def test_state_key_scopes_relevance_to_that_states_own_outgoing_triggers(client):
    project_id = _upload(client, "two_state_scoped_test", TWO_STATE_PROJECT)

    response_a = client.get(f"/api/core/projects/{project_id}/signals?state_key=a")
    by_name_a = {s["signal"]["name"]: s for s in response_a.json()["signals"]}
    assert by_name_a["progressSignal"]["relevant"] is True
    assert by_name_a["moodSignal"]["relevant"] is False

    response_b = client.get(f"/api/core/projects/{project_id}/signals?state_key=b")
    by_name_b = {s["signal"]["name"]: s for s in response_b.json()["signals"]}
    assert by_name_b["progressSignal"]["relevant"] is False
    assert by_name_b["moodSignal"]["relevant"] is True


def test_an_unknown_state_key_falls_back_to_every_states_triggers_combined(client):
    project_id = _upload(client, "unknown_state_key_test", TWO_STATE_PROJECT)

    response = client.get(f"/api/core/projects/{project_id}/signals?state_key=not-a-real-state")

    by_name = {s["signal"]["name"]: s for s in response.json()["signals"]}
    assert by_name["progressSignal"]["relevant"] is True
    assert by_name["moodSignal"]["relevant"] is True


def test_a_state_tracking_all_signals_reports_every_one_relevant_and_leaves_the_others_scoped(client):
    project = TWO_STATE_PROJECT.replace(
        '  b:\n    input-processor: ai\n    contextual-prompt: "middle"', '  b:\n    signal-tracking-strategy: all\n    input-processor: ai\n    contextual-prompt: "middle"',
    )
    project_id = _upload(client, "two_state_all_test", project)

    by_name_b = {s["signal"]["name"]: s for s in client.get(f"/api/core/projects/{project_id}/signals?state_key=b").json()["signals"]}
    assert {name for name, s in by_name_b.items() if s["relevant"]} == {"progressSignal", "moodSignal", "unusedSignal"}

    by_name_a = {s["signal"]["name"]: s for s in client.get(f"/api/core/projects/{project_id}/signals?state_key=a").json()["signals"]}
    assert {name for name, s in by_name_a.items() if s["relevant"]} == {"progressSignal"}

    by_name = {s["signal"]["name"]: s for s in client.get(f"/api/core/projects/{project_id}/signals").json()["signals"]}
    assert by_name["unusedSignal"]["relevant"] is True


def test_ai_memory_scope_is_a_state_field_the_editor_sets_and_the_graph_reports(client):
    project_id = _upload(client, "ai_memory_scope_edit_test", TWO_STATE_PROJECT)

    def scope_of(state_key):
        nodes = client.get(f"/api/skills/platform/projects/{project_id}/graph").json()["nodes"]
        return next(n["ai_memory_scope"] for n in nodes if n["state"]["key"] == state_key)

    assert scope_of("b") == "none"

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/b/ai-memory-scope", json={"value": "local"})
    assert response.status_code == 200, response.text
    assert scope_of("b") == "local"
    assert "ai-memory-scope: local" in client.get(f"/api/skills/platform/projects/{project_id}/files/index.yml").json()["content"]

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/b/ai-memory-scope", json={"value": "wipe"})
    assert response.status_code == 400
    assert scope_of("b") == "local"


def test_signal_tracking_strategy_is_a_state_field_the_editor_sets_and_the_graph_reports(client):
    project_id = _upload(client, "signal_tracking_strategy_edit_test", TWO_STATE_PROJECT)

    def strategy_of(state_key):
        nodes = client.get(f"/api/skills/platform/projects/{project_id}/graph").json()["nodes"]
        return next(n["signal_tracking_strategy"] for n in nodes if n["state"]["key"] == state_key)

    assert strategy_of("b") == "relevant"

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/b/signal-tracking-strategy", json={"value": "all"})
    assert response.status_code == 200, response.text
    assert strategy_of("b") == "all"
    assert "signal-tracking-strategy: all" in client.get(f"/api/skills/platform/projects/{project_id}/files/index.yml").json()["content"]

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/b/signal-tracking-strategy", json={"value": "some"})
    assert response.status_code == 400
    assert strategy_of("b") == "all"


def test_input_processor_is_a_state_field_the_editor_sets_and_the_graph_reports(client):
    project_id = _upload(client, "input_processor_edit_test", TWO_STATE_PROJECT)

    def processor_of(state_key):
        nodes = client.get(f"/api/skills/platform/projects/{project_id}/graph").json()["nodes"]
        return next(n["state"]["input_processor"] for n in nodes if n["state"]["key"] == state_key)

    assert processor_of("c") == "ai"

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/c/input-processor", json={"value": "system"})
    assert response.status_code == 200, response.text
    assert processor_of("c") == "system"
    assert "input-processor: system" in client.get(f"/api/skills/platform/projects/{project_id}/files/index.yml").json()["content"]

    response = client.put(f"/api/skills/platform/projects/{project_id}/states/c/input-processor", json={"value": "human"})
    assert response.status_code == 400
    assert processor_of("c") == "system"


def test_a_state_added_from_the_editor_is_the_automaton_s_own(client):
    project_id = _upload(client, "new_state_processor_test", TWO_STATE_PROJECT)

    added = client.post(f"/api/skills/platform/projects/{project_id}/states").json()

    assert added["input_processor"] == "system"
    assert added["chat_enabled"] is False
    nodes = client.get(f"/api/skills/platform/projects/{project_id}/graph").json()["nodes"]
    assert next(n["state"]["input_processor"] for n in nodes if n["state"]["key"] == added["key"]) == "system"
