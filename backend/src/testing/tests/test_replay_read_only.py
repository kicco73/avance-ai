from __future__ import annotations

import re
import time

import pytest

from conftest import FakeAiService, chat_action, chat_turn, enter_chat, parse_sse_result, session_of

pytestmark = pytest.mark.contract

YML = """
project:
  id: proj
init-action:
  target: talk
signals:
  mood:
    definition: How happy the user is, 0-100.
states:
  talk:
    input-processor: ai
    contextual-prompt: Talk.
    signal-tracking-strategy: all
    actions:
      - name: judge
        ui-button: Judge
        target: judging
  judging:
    input-processor: ai
    contextual-prompt: Judge.
    signal-tracking-strategy: read-only
    actions:
      - name: celebrate
        target: done
        trigger: "signal.mood >= 50"
  done:
    input-processor: ai
    contextual-prompt: Bye.
    actions:
      - name: again
        ui-button: Again
        target: talk
"""


class _MoodAiService(FakeAiService):
    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append((system_prompt, history))
        if "signals" in schema:
            said = [int(m) for m in re.findall(r"mood (\d+)", history[-1]["content"] if history else "")]
            on_metadata("signals", {"mood": said[-1]} if said else {})
        yield "Fake AI reply."


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return _MoodAiService()


def test_a_replay_gives_a_read_only_state_the_last_values_it_measured(client, app_db):
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "mood 70")
    chat_action(client, session_id, "judge")
    turn = chat_turn(client, session_id, "hello")
    assert turn["new_state"] == "done"
    user_message_id = next(m["id"] for m in app_db.get_messages(session_id) if m["content"] == "hello")
    client.put(f"/api/skills/platform/messages/{user_message_id}/expected-state", json={"expected_state": "done"})

    run = client.post(
        f"/api/skills/testing/projects/{project_id}/tests", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    deadline = time.monotonic() + 10.0
    while run["status"] not in ("completed", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        run = client.get(f"/api/skills/testing/projects/{project_id}/tests/{run['id']}").json()

    results = {result["name"]: result for result in run["results"]}
    assert (results["state_accuracy"]["value"], results["state_accuracy"]["sample_count"]) == (100.0, 1)
