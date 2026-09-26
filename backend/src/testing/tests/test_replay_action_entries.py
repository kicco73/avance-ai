from __future__ import annotations

import io
import json
import re
import time
import zipfile

import pytest

from conftest import FakeAiService, installed_skill, parse_sse_result

pytestmark = pytest.mark.contract

_INDEX_YML = """
project:
  id: replay_actions
init-action:
  target: welcome
signals:
  mood:
    definition: How happy the user is, 0-100.
states:
  welcome:
    input-processor: ai
    contextual-prompt: Welcome.
    actions:
      - name: start
        ui-button: Start
        target: opening
  opening:
    input-processor: ai
    contextual-prompt: Talk.
    actions:
      - name: cheer
        target: happy
        trigger: "signal.mood >= 50"
  happy:
    input-processor: ai
    contextual-prompt: Smile.
    actions:
      - name: evaluate
        ui-button: Evaluate
        target: evaluation
  evaluation:
    input-processor: ai
    contextual-prompt: Done.
    actions:
      - name: again
        ui-button: Again
        target: opening
"""

_TURN = re.compile(r"\[Turn (\d+)\]\nUser: mood (\d+)")


class _MoodAiService(FakeAiService):
    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append((system_prompt, history))
        turns = _TURN.findall(system_prompt.full_text())
        if turns:
            rows = "\n".join(f"{turn},{mood}" for turn, mood in turns)
            on_metadata("signals", f"turn,mood\n{rows}\n[eof]")
        else:
            said = [int(m) for m in re.findall(r"mood (\d+)", history[-1]["content"])]
            on_metadata("signals", {"mood": said[-1]} if said else {})
        yield "Fake AI reply."


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return _MoodAiService()


@pytest.fixture
def project_id(client) -> str:
    installed_skill("avance_platform")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.yml", _INDEX_YML)
    response = client.post(
        "/api/skills/platform/projects/upload", content=buffer.getvalue(), headers={"Content-Type": "application/zip"},
    )
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def _at(second: int) -> str:
    return f"2026-09-26T09:00:{second:02d}+00:00"


def _import(client, project_id, messages: list[dict]) -> int:
    session = {
        "name": "replayed", "type": "imported", "start_state": "welcome", "labeled": True,
        "timestamp": _at(0), "datetime_end": _at(59), "messages": messages,
    }
    response = client.post(
        f"/api/skills/platform/projects/{project_id}/sessions/import",
        files=[("files", ("s.json", json.dumps([session]), "application/json"))],
    )
    session_id = parse_sse_result(response)["last_session_id"]
    assert session_id is not None
    return session_id


def _replay(client, project_id, session_id, strategy: str = "turn_by_turn") -> dict:
    run = client.post(
        f"/api/skills/testing/projects/{project_id}/tests", json={"session_id": session_id, "strategy": strategy},
    ).json()
    deadline = time.monotonic() + 10.0
    while run["status"] not in ("completed", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        run = client.get(f"/api/skills/testing/projects/{project_id}/tests/{run['id']}").json()
    assert run["status"] == "completed", run
    return {result["name"]: result for result in run["results"]}


def _accuracy(results: dict) -> tuple:
    return results["state_accuracy"]["value"], results["state_accuracy"]["sample_count"]


def test_a_replay_fires_the_button_that_leaves_the_first_state_then_plays_the_conversation(client, project_id):
    session_id = _import(client, project_id, [
        {"role": "assistant", "text": "Welcome!", "timestamp": _at(1)},
        {"role": "action", "action": "start", "timestamp": _at(2), "expected_state": "opening"},
        {"role": "assistant", "text": "Let's talk.", "timestamp": _at(3)},
        {"role": "user", "text": "mood 10", "timestamp": _at(4), "expected_state": "opening"},
        {"role": "assistant", "text": "I see.", "timestamp": _at(5)},
        {"role": "user", "text": "mood 80", "timestamp": _at(6), "expected_state": "happy"},
        {"role": "assistant", "text": "Great.", "timestamp": _at(7)},
        {"role": "action", "action": "evaluate", "timestamp": _at(8), "expected_state": "evaluation"},
    ])

    assert _accuracy(_replay(client, project_id, session_id)) == (100.0, 4)


def test_an_action_the_replay_cannot_take_from_where_it_is_is_not_fired_and_scores_a_mismatch(client, project_id):
    session_id = _import(client, project_id, [
        {"role": "action", "action": "evaluate", "timestamp": _at(1), "expected_state": "evaluation"},
        {"role": "user", "text": "mood 10", "timestamp": _at(2), "expected_state": "welcome"},
    ])

    assert _accuracy(_replay(client, project_id, session_id)) == (50.0, 2)


@pytest.mark.parametrize("strategy", ["turn_by_turn", "batch", "batch_lite"])
def test_signal_values_land_on_their_own_user_turns_with_action_entries_between_them(client, project_id, strategy):
    session_id = _import(client, project_id, [
        {"role": "action", "action": "start", "timestamp": _at(1)},
        {"role": "user", "text": "mood 10", "timestamp": _at(2), "expected_state": "opening"},
        {"role": "assistant", "text": "I see.", "timestamp": _at(3)},
        {"role": "action", "action": "evaluate", "timestamp": _at(4)},
        {"role": "user", "text": "mood 80", "timestamp": _at(5), "expected_state": "happy"},
        {"role": "assistant", "text": "Great.", "timestamp": _at(6)},
        {"role": "user", "text": "mood 20", "timestamp": _at(7), "expected_state": "happy"},
    ])

    assert _accuracy(_replay(client, project_id, session_id, strategy)) == (100.0, 3)
