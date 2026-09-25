from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, State
from system import bus
from system.bus import INPUT_BUTTON, INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from scripted_provider import Reply, ScriptedProvider
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.contract


def _automaton(*, trigger: str | None, after_ai_message: bool) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    go = Action(name="go", ui_label="Go", ui_button="Go", target="b", trigger=trigger, on_exit="chat.write('Step 2')")
    states = {
        "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
        "a": State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="in a", actions=[go]),
        "b": State(input_processor="ai", key="b", ui_label="B", final=False, contextual_prompt="in b", actions=[]),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments=(),
        autotracking_on_ai_message=after_ai_message, sources=[], project_id=PROJECT_ID,
    )


async def _said(turn_service_for, automaton, provider, message_type: str, body: dict) -> list[tuple[str, bool]]:
    db = turn_service_for.db
    turn_service = turn_service_for(automaton, provider)
    frames: list[Message] = []

    async def take(message: Message) -> None:
        frames.append(message)

    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, "live")
    bus.subscribe("output.text", take)
    TurnInput(turn_service, db).register()
    await bus.publish(Message(
        type=message_type, body=body, username=WebSession().user, session_id=session["id"], channel="webchat",
        origin_id="c1",
    ))
    for _ in range(200):
        if any(frame.body.get("answer", True) for frame in frames):
            break
        await asyncio.sleep(0.01)
    return [(frame.body["text"], frame.body.get("answer", True)) for frame in frames]


def test_a_trigger_that_writes_before_the_model_answers_puts_the_text_in_its_own_message_first(turn_service_for):
    said = asyncio.run(_said(
        turn_service_for, _automaton(trigger="True", after_ai_message=False),
        ScriptedProvider(Reply("from b")), INPUT_TEXT, {"text": "hello"},
    ))

    assert said == [("Step 2", False), ("from b", True)]


def test_a_trigger_that_fires_after_the_model_answered_puts_the_text_in_its_own_message_after(turn_service_for):
    said = asyncio.run(_said(
        turn_service_for, _automaton(trigger="True", after_ai_message=True),
        ScriptedProvider(Reply("from a")), INPUT_TEXT, {"text": "hello"},
    ))

    assert said[:2] == [("from a", True), ("Step 2", False)]


def test_a_button_into_an_ai_state_writes_its_own_message_before_the_model_s_reply(turn_service_for):
    said = asyncio.run(_said(
        turn_service_for, _automaton(trigger=None, after_ai_message=False),
        ScriptedProvider(Reply("from b")), INPUT_BUTTON, {"id": "go"},
    ))

    assert said == [("Step 2", False), ("from b", True)]
