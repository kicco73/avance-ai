from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from conftest import installed_skill, parse_sse_result

pytestmark = pytest.mark.contract

_INDEX_YML = """avance-version: "1.7.0"

init-action:
  target: Start
  on-exit: env.threshold = 2

env:
  threshold:
    type: number

states:
  Start:
    input-processor: ai
    contextual-prompt: |
      Say hi.
    actions:
      - name: go
        target: Second
        trigger: "env.threshold >= 2"
  Second:
    input-processor: ai
    contextual-prompt: |
      Say bye.
    actions:
      - name: stay
        target: Second
        trigger: "False"
project:
  ui-label: Threshold
  id: threshold_world
"""


@pytest.fixture
def threshold_project(client) -> str:
    installed_skill("avance_platform")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.yml", _INDEX_YML)
    response = client.post(
        "/api/skills/platform/projects/upload", content=buffer.getvalue(),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def _import_session(client, project_id, start_state, expected_state) -> int:
    session = {
        "name": "replayed", "type": "imported", "start_state": start_state, "labeled": True,
        "timestamp": "2026-09-26T04:20:00+00:00", "datetime_end": "2026-09-26T04:30:00+00:00",
        "messages": [
            {"role": "user", "text": "hi", "timestamp": "2026-09-26T04:20:35+00:00", "expected_state": expected_state},
            {"role": "assistant", "text": "hello", "timestamp": "2026-09-26T04:20:40+00:00"},
        ],
    }
    response = client.post(
        f"/api/skills/platform/projects/{project_id}/sessions/import",
        files=[("files", ("s.json", json.dumps([session]), "application/json"))],
    )
    return parse_sse_result(response)["last_session_id"]


def _replay(client, project_id, session_id) -> dict:
    run = client.post(
        f"/api/skills/testing/projects/{project_id}/tests", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    deadline = time.monotonic() + 5.0
    while run["status"] not in ("completed", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        run = client.get(f"/api/skills/testing/projects/{project_id}/tests/{run['id']}").json()
    assert run["status"] == "completed", run
    return {result["name"]: result for result in run["results"]}


def test_a_replay_runs_the_init_action_so_a_trigger_reading_its_env_can_fire(client, threshold_project):
    session_id = _import_session(client, threshold_project, "Start", "Second")

    results = _replay(client, threshold_project, session_id)

    assert (results["state_accuracy"]["value"], results["state_accuracy"]["sample_count"]) == (100.0, 1)


def test_a_replay_leaves_the_real_sessions_env_and_tracking_rows_untouched(client, threshold_project):
    session_id = _import_session(client, threshold_project, "Start", "Second")
    signals_before = client.get(f"/api/core/sessions/{session_id}/signals").json()
    env_before = client.get(f"/api/skills/platform/sessions/{session_id}/env").json()

    _replay(client, threshold_project, session_id)

    assert client.get(f"/api/core/sessions/{session_id}/signals").json() == signals_before
    assert client.get(f"/api/skills/platform/sessions/{session_id}/env").json() == env_before


def test_a_replay_starts_from_the_sessions_start_state_not_from_its_first_label(client, threshold_project):
    session_id = _import_session(client, threshold_project, "Start", "Second")

    results = _replay(client, threshold_project, session_id)

    assert results["state_accuracy_transition"]["sample_count"] == 1
    assert results["transition_responsiveness"]["value"] > 0.0
