from __future__ import annotations

import json
import re

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
  pace:
    definition: How fast the user answers, 0-100.
env:
  final_report:
    type: string
    ai-definition: The final report.
  seen_mood:
    type: number
  pace_missing:
    type: bool
states:
  talk:
    input-processor: ai
    contextual-prompt: Talk.
    signal-tracking-strategy: all
    actions:
      - name: finish
        ui-button: Finish
        target: evaluation
  evaluation:
    input-processor: ai
    contextual-prompt: Evaluate.
    signal-tracking-strategy: read-only
    output: [final_report]
    actions:
      - name: report
        ui-button: Report
        target: evaluation
        on-exit: |
          env.seen_mood = signal.mood
          env.pace_missing = signal.pace is None
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
        if "output" in schema:
            on_metadata("output", {"final_report": "done"})
        yield "Fake AI reply."


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    return _MoodAiService()


@pytest.fixture
def session_id(client) -> int:
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "mood 70")
    chat_action(client, session_id, "finish")
    return session_id


def _values(row: dict):
    return json.loads(row["values"]) if row["values"] is not None else None


def test_a_turn_that_measured_signals_records_them_as_before(session_id, app_db):
    measured = [_values(row) for row in app_db.get_signals(session_id) if row["old_state"] is None and row["values"]]

    assert measured == [{"mood": 70}]


def test_a_read_only_turn_with_an_output_records_no_signal_values(session_id, app_db):
    [row] = [row for row in app_db.get_signals(session_id) if row["output"]]

    assert row["values"] is None


def test_a_manual_action_in_a_read_only_state_sees_the_last_measured_values(client, session_id):
    chat_action(client, session_id, "report")

    env = client.get(f"/api/skills/platform/sessions/{session_id}/env").json()["action_set"]
    assert (env["seen_mood"], env["pace_missing"]) == (70, True)


def test_a_trigger_in_a_read_only_state_fires_on_the_last_measured_value(client, session_id):
    chat_action(client, session_id, "judge")

    assert chat_turn(client, session_id, "hello")["new_state"] == "done"
