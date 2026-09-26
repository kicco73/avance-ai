from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from ai.turn.signals_prompt_block import SignalsPromptBlock
from conftest import chat_turn, enter_chat, parse_sse_result, session_of

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "signals:\n"
    "  mood:\n    definition: How happy the user is, 0-100.\n"
    "  pace:\n    definition: How fast the user answers, 0-100.\n"
    "states:\n"
    "  a:\n"
    "    input-processor: ai\n"
    "    contextual-prompt: hi\n"
    "    signal-tracking-strategy: read-only\n"
    "    actions:\n"
    "      - name: stay\n"
    "        target: a\n"
)


def _system_prompt(fake_ai_service) -> str:
    system_prompt, _ = fake_ai_service.calls[-1]
    return system_prompt.full_text()


def test_a_read_only_state_measures_nothing_and_shows_every_signal_with_its_definition_none_included(client, fake_ai_service):
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200

    chat_turn(client, session_of(enter_chat(client, project_id)), "hello")

    prompt = _system_prompt(fake_ai_service)
    assert "mood: None\n\tHow happy the user is, 0-100." in prompt
    assert "pace: None\n\tHow fast the user answers, 0-100." in prompt
    assert "Definition of signals" not in prompt


def test_the_block_carries_the_last_measured_value_of_each_signal():
    automaton = AutomatonBuilder().build({"index.yml": YML})

    block = SignalsPromptBlock.for_state(automaton, automaton.states["a"], {"mood": 80})

    assert block is not None
    assert block.lines() == {
        "signal.mood": "mood: 80\n\tHow happy the user is, 0-100.",
        "signal.pace": "pace: None\n\tHow fast the user answers, 0-100.",
    }
