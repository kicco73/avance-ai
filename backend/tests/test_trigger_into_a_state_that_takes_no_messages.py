from __future__ import annotations

import asyncio
import json

import pytest

from ai.llm_provider import LLMProvider
from automaton.automaton import Action, Automaton, EnvKey, State
from system import bus
from system.bus import INPUT_TEXT, Message
from system.web_session import WebSession
from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

REPORT = "| Señal | Valor |\n|---|---|\n| Empatía | 40 |"


class ScriptedByState(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.asked_for: list[list[str]] = []

    async def stream_json(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        output = schema.get("output")
        names = sorted(output.fields) if output is not None else []
        self.asked_for.append(names)
        replies = {
            ("fin",): {"text": "Pues me voy.", "output": {"fin": True}},
            ("informe",): {"text": "Te he preparado el informe.", "output": {"informe": REPORT}},
        }
        yield json.dumps(replies.get(tuple(names), {"text": "..."}), ensure_ascii=False)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def _automaton(*, evaluation_chat_enabled: bool) -> Automaton:
    finished = Action(name="finished", ui_label="Finished", ui_button="", target="evaluacion", trigger="env.fin")
    conversation = State(
        input_processor="ai", key="a", ui_label="Conversation", final=False, contextual_prompt="Play the patient.",
        actions=[finished], output=("fin",),
    )
    show = Action(name="show", ui_label="Show", ui_button="Show", target="end")
    evaluation = State(
        input_processor="ai", key="evaluacion", ui_label="Evaluation", final=False, contextual_prompt="Write the report.",
        actions=[show], output=("informe",), chat_enabled=evaluation_chat_enabled,
    )
    end = State(input_processor="system", key="end", ui_label="End", final=True, chat_enabled=False)
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="system", key="", ui_label="", final=False, actions=[init_action]),
            "a": conversation, "evaluacion": evaluation, "end": end,
        },
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=True, project_id=PROJECT_ID,
        env_keys=[
            EnvKey(name="fin", type="bool", ai_definition="Whether the patient left."),
            EnvKey(name="informe", type="string", ai_definition="The final report, in Markdown."),
        ],
    )


async def _session(turn_service_for, *, evaluation_chat_enabled: bool) -> tuple[ScriptedByState, int]:
    db = turn_service_for.db
    provider = ScriptedByState()
    turn_service = turn_service_for(_automaton(evaluation_chat_enabled=evaluation_chat_enabled), provider)
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session_id = (await turn_service.enter_session(PROJECT_ID, "live"))["id"]
    TurnInput(turn_service, db).register()
    return provider, session_id


async def _say(session_id: int, text: str, replies: int) -> list[str]:
    said: list[str] = []
    done = asyncio.Event()

    async def take(message: Message) -> None:
        if message.session_id == session_id:
            said.append(message.body["text"])
            if len(said) == replies:
                done.set()

    bus.subscribe("output.text", take)
    try:
        await bus.publish(Message(
            type=INPUT_TEXT, body={"text": text}, username=WebSession().user, session_id=session_id,
            channel="webchat", origin_id="connection-1",
        ))
        await asyncio.wait_for(done.wait(), timeout=2)
    finally:
        bus.unsubscribe("output.text", take)
    return said


async def test_a_trigger_after_the_reply_into_a_state_that_takes_no_messages_lets_that_state_speak(turn_service_for):
    provider, session_id = await _session(turn_service_for, evaluation_chat_enabled=False)

    said = await _say(session_id, "adiós", replies=2)

    assert said == ["Pues me voy.", "Te he preparado el informe."]
    assert provider.asked_for == [["fin"], ["informe"]]
    env = PersistedEnv(turn_service_for.db, FixedProjectContext(project_id=PROJECT_ID), session_id).action_set()
    assert env["informe"] == REPORT


async def test_a_state_that_takes_messages_still_waits_for_the_person(turn_service_for):
    provider, session_id = await _session(turn_service_for, evaluation_chat_enabled=True)

    first = await _say(session_id, "adiós", replies=1)
    second = await _say(session_id, "¿y el informe?", replies=1)

    assert (first, second) == (["Pues me voy."], ["Te he preparado el informe."])
    assert provider.asked_for == [["fin"], ["informe"]]


def test_a_turn_that_moved_before_its_reply_owes_nothing_more(turn_service_for):
    turn_service = turn_service_for(_automaton(evaluation_chat_enabled=False), ScriptedByState())

    owed = turn_service.entered_a_state_owed_its_turn(
        {"session_id": 1, "state_changed": True, "moved_before_reply": True},
    )

    assert owed is None
