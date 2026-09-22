"""A state that answers through its own scripts never costs a model call.

`a` is the model's; `step` is the automaton's: chat is off there, a
button moves it, and what the pressed action's on-exit wrote with
chat.write is what the person reads — saved and published exactly like
a reply the model would have written.
"""
from __future__ import annotations

import asyncio

import pytest

from ai import AiService
from automaton.automaton_builder import AutomatonBuilder
from system import bus
from system.bus import INPUT_BUTTON, INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from scripted_provider import Reply, ScriptedProvider
from turn_harness import PROJECT_ID, TURN_FRAMES, _is_terminal, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.contract

INDEX_YML = """\
project:
  id: proj
  signal-tracking-on-ai-message: {on_ai_message}
init-action:
  target: {initial}
  on-exit: |
    env.step = 0
    {init_on_exit}
env:
  step:
    type: number
  frequency:
    type: list
states:
  a:
    input-processor: ai
    contextual-prompt: greet
    chat-enabled: {hello_chat}
    actions:
      - name: start
        target: step
        on-exit: |
          env.step = 1
          {start_on_exit}
      - name: leap
        target: step
        trigger: "{leap_trigger}"
        on-exit: |
          env.step = 1
          chat.write('Step 1')
      - name: again
        target: a
        on-exit: env.step = env.step
  step:
    input-processor: system
    contextual-prompt: ignored
    actions:
      - name: next
        target: step
        on-exit: |
          env.step = env.step + 1
          chat.write('Step %d' % env.step)
      - name: end
        target: done
        trigger: env.step >= 3
        on-exit: chat.write('Done')
      - name: pick
        target: step
        trigger: choice.frequency
        on-exit: |
          env.step = env.step + 10
          chat.write('Picked ' + choice.frequency)
  done:
    input-processor: system
"""


def _automaton(**parts):
    filled = {
        "on_ai_message": "false", "initial": "a", "init_on_exit": "env.frequency = ['Never', 'Often']",
        "start_on_exit": "chat.write('Step 1')", "leap_trigger": "False", "hello_chat": "false", **parts,
    }
    return AutomatonBuilder().build({"index.yml": INDEX_YML.format(**filled)})


class _Frames:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.settled = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.messages.append(message)
        if _is_terminal(self.types()):
            self.settled.set()

    def types(self) -> list[str]:
        return [m.type for m in self.messages]

    def texts(self) -> list[str]:
        return [m.body["text"] for m in self.messages if m.type == "output.text"]

    def last(self) -> Message:
        return self.messages[-1]


class _Conversation:
    def __init__(self, turn_service_for, automaton, provider) -> None:
        self.db = turn_service_for.db
        self.provider = provider
        self.turn_service = turn_service_for(automaton, ai_service=AiService(provider))
        self.frames = _Frames()
        self.session_id = 0

    async def open(self) -> "_Conversation":
        self.db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
        session = await self.turn_service.enter_session(PROJECT_ID, 'live')
        self.session_id = session["id"]
        for message_type in TURN_FRAMES:
            bus.subscribe(message_type, self.frames.take)
        TurnInput(self.turn_service, self.db).register()
        return self

    async def presses(self, button: str) -> None:
        await self._exchange(INPUT_BUTTON, id=button)

    async def says(self, text: str) -> None:
        await self._exchange(INPUT_TEXT, text=text)

    async def _exchange(self, type: str, **body) -> None:
        self.frames.messages.clear()
        self.frames.settled.clear()
        await bus.publish(Message(
            type=type, body=body, username=WebSession().user, session_id=self.session_id, channel="webchat", origin_id="c1",
        ))
        await asyncio.wait_for(self.frames.settled.wait(), timeout=10)

    def transcript(self) -> list[tuple[str, str]]:
        return [(m["role"], m["content"]) for m in self.db.get_messages(self.session_id)]

    def state(self) -> str | None:
        return self.db.get_current_state_for_session(self.session_id)


async def test_a_button_into_a_system_state_answers_with_what_the_script_wrote(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(), ScriptedProvider()).open()

    await chat.presses("start")

    assert chat.frames.texts() == ["Step 1"]
    assert chat.frames.last().type == "state.buttons"
    assert chat.frames.last().body["actions"] and chat.state() == "step"
    assert chat.transcript()[-1] == ("assistant", "Step 1")
    assert chat.frames.messages[-2].body["assistant_message_id"] == chat.db.get_messages(chat.session_id)[-1]["id"]
    assert chat.provider.started == 0

    await chat.presses("next")

    assert chat.frames.texts() == ["Step 2"]
    assert chat.transcript()[-1] == ("assistant", "Step 2")
    assert chat.provider.started == 0


async def test_a_self_loop_button_in_the_model_s_state_asks_nothing_of_the_model_and_says_nothing(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(), ScriptedProvider(Reply("unwanted"))).open()
    before = chat.transcript()

    await chat.presses("again")

    assert chat.frames.texts() == []
    assert chat.frames.last().type == "state.buttons"
    assert chat.state() == "a"
    assert chat.transcript() == before
    assert chat.provider.started == 0


async def test_text_has_no_effect_in_a_system_state(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(), ScriptedProvider()).open()
    await chat.presses("start")

    await chat.says("hi")

    assert chat.frames.last().type == "output.error"
    assert chat.frames.last().body["code"] == "state_not_chat"
    assert ("user", "hi") not in chat.transcript()
    assert chat.provider.started == 0


async def test_a_choice_and_an_env_trigger_move_a_system_state_without_the_model(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(), ScriptedProvider()).open()
    await chat.presses("start")

    await chat.presses("choice:frequency:1")

    assert chat.frames.texts() == ["Picked Often"]
    assert chat.state() == "step"

    await chat.presses("choice:frequency:0")

    assert chat.frames.texts() == ["Done"]
    assert chat.state() == "done"
    assert chat.provider.started == 0


async def test_a_trigger_from_the_model_s_state_lands_on_the_script_s_reply(turn_service_for):
    automaton = _automaton(leap_trigger="True", hello_chat="true")
    chat = await _Conversation(turn_service_for, automaton, ScriptedProvider(Reply("thinking..."))).open()

    await chat.says("hi")

    assert chat.frames.texts() == ["Step 1"]
    assert chat.state() == "step"
    assert chat.transcript()[-2:] == [("user", "hi"), ("assistant", "Step 1")]
    assert chat.provider.started == 0


async def test_with_tracking_after_the_reply_the_script_s_text_follows_the_model_s(turn_service_for):
    automaton = _automaton(leap_trigger="True", hello_chat="true", on_ai_message="true")
    chat = await _Conversation(turn_service_for, automaton, ScriptedProvider(Reply("thinking..."))).open()

    await chat.says("hi")

    assert chat.frames.texts() == ["thinking...\n\nStep 1"]
    assert chat.state() == "step"
    assert chat.provider.started == 1


async def test_the_opening_message_of_a_system_initial_state_is_the_init_action_s(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(initial="step", init_on_exit="chat.write('Welcome')"), ScriptedProvider()).open()

    result = await chat.turn_service.open_conversation(chat.session_id)

    assert [m["content"] for m in result["reply"]] == ["Welcome"]
    assert chat.transcript() == [("assistant", "Welcome")]
    assert chat.provider.started == 0


async def test_an_action_that_writes_nothing_leaves_no_reply_at_all(turn_service_for):
    chat = await _Conversation(turn_service_for, _automaton(start_on_exit="env.step = 1"), ScriptedProvider()).open()
    before = chat.transcript()

    await chat.presses("start")

    assert chat.frames.texts() == []
    assert chat.frames.last().type == "state.buttons"
    assert chat.transcript() == before
    assert chat.state() == "step"
    assert chat.provider.started == 0
