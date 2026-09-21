"""A provider that goes quiet costs the person five seconds, not thirteen minutes.

Session 55 in production (2026-09-21, backend/src/stuck.db): Derek
answered question 8, the transition to step 9 was persisted, Gemini
never sent a byte, and the exchange ended in silence — the person waited
13 minutes for an answer that had already been discarded. Replayed here
with a scripted provider on a clock the test moves (virtual_clock.py):
the same choice, the same silence, and the answer arriving 13 minutes
late. The exchange must end with `output.error` at 5.0 s and nothing
may land after it.
"""
from __future__ import annotations

import asyncio

import pytest

from ai import AiService
from ai._providers.cascading_llm_provider import AutoLiveLLMProvider
from automaton.automaton import Action, Automaton, State
from system import bus
from system.bus import INPUT_BUTTON, INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from scripted_provider import Reply, RepliesAfter, ScriptedProvider, Stalled, StallsAfter, Trickles
from turn_harness import PROJECT_ID, TURN_FRAMES, turn_service_for  # noqa: F401 — a fixture, used by name
from virtual_clock import VirtualClockLoop

pytestmark = pytest.mark.regression

FIRST_BYTE_SECONDS = 5.0
NEXT_BYTE_SECONDS = 10.0
DEREK_WAITED_SECONDS = 13 * 60


def _automaton() -> Automaton:
    go = Action(name="go", ui_label="Go", ui_button="Go", target="b")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="Question 8", final=False, contextual_prompt="ask 8", actions=[go]),
        "b": State(key="b", ui_label="Question 9", final=False, contextual_prompt="ask 9", actions=[], history_cutoff=True),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments=(),
        autotracking_on_ai_message=False, sources=[], project_id=PROJECT_ID,
    )


class _Frames:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def take(self, message: Message) -> None:
        self.messages.append(message)

    def types(self) -> list[str]:
        return [m.type for m in self.messages]

    def last(self) -> Message:
        return self.messages[-1]

    def texts(self) -> list[str]:
        return [m.body["text"] for m in self.messages if m.type == "output.text"]

    def streamed(self) -> str:
        return "".join(m.body["text"] for m in self.messages if m.type == "output.text_stream")


class _Conversation:
    def __init__(self, turn_service_for, provider=None, ai_service=None) -> None:
        self.db = turn_service_for.db
        self.turn_service = turn_service_for(_automaton(), provider, ai_service=ai_service)
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
        await bus.publish(self._sent(INPUT_BUTTON, id=button))

    async def says(self, text: str) -> None:
        await bus.publish(self._sent(INPUT_TEXT, text=text))

    def _sent(self, type: str, **body) -> Message:
        return Message(
            type=type, body=body, username=WebSession().user, session_id=self.session_id, channel="webchat", origin_id="c1",
        )

    def persisted_state(self) -> str | None:
        return self.db.get_current_state_for_session(self.session_id)


@pytest.fixture
def clocked():
    with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
        yield runner


def _run(clocked, scenario) -> None:
    clocked.run(scenario(clocked.get_loop().clock))


def test_derek_is_told_at_five_seconds_and_nothing_lands_thirteen_minutes_later(clocked, turn_service_for):
    provider = ScriptedProvider(RepliesAfter(DEREK_WAITED_SECONDS, "Question 9: ..."))

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.presses("go")
        await clock.advance(FIRST_BYTE_SECONDS - 0.1)

        assert derek.persisted_state() == "b"
        assert "output.error" not in derek.frames.types()
        assert "output.text" not in derek.frames.types()

        await clock.advance(0.1)

        assert clock.now == FIRST_BYTE_SECONDS
        assert derek.frames.last().type == "output.error"
        assert derek.frames.last().body["message"] == "Unexpected server error."
        assert f"sent nothing for {FIRST_BYTE_SECONDS:g}s" in derek.frames.last().body["detail"]
        assert "output.text" not in derek.frames.types()
        assert provider.torn_down == 1
        told_by_then = list(derek.frames.messages)

        await clock.advance(DEREK_WAITED_SECONDS)

        assert derek.frames.messages == told_by_then
        assert provider.finished == 0

    _run(clocked, scenario)


def test_the_error_names_no_state_change_although_the_transition_was_persisted(clocked, turn_service_for):
    provider = ScriptedProvider(Stalled())

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.presses("go")
        await clock.advance(FIRST_BYTE_SECONDS)

        assert derek.persisted_state() == "b"
        assert derek.frames.types() == ["output.text_stream", "output.error"]

    _run(clocked, scenario)


def test_after_the_stall_the_next_request_is_answered(clocked, turn_service_for):
    provider = ScriptedProvider(Stalled(), Reply("Question 9: ..."))

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.presses("go")
        await clock.advance(FIRST_BYTE_SECONDS)
        assert derek.frames.last().type == "output.error"

        await derek.says("still there?")
        await clock.settle()

        assert derek.frames.texts() == ["Question 9: ..."]
        assert derek.frames.last().type == "state.buttons"

    _run(clocked, scenario)


def test_a_text_turn_whose_provider_stalls_ends_the_same_way(clocked, turn_service_for):
    provider = ScriptedProvider(Stalled(), Reply("Back again."))

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.says("my answer to question 8")
        await clock.advance(FIRST_BYTE_SECONDS - 0.1)
        assert "output.error" not in derek.frames.types()

        await clock.advance(0.1)

        assert derek.frames.last().type == "output.error"
        assert f"sent nothing for {FIRST_BYTE_SECONDS:g}s" in derek.frames.last().body["detail"]
        assert len(derek.db.get_messages(derek.session_id)) == 1

        await derek.says("hello?")
        await clock.settle()

        assert derek.frames.texts() == ["Back again."]

    _run(clocked, scenario)


def test_a_reply_that_goes_quiet_half_way_is_given_up_ten_seconds_after_its_last_byte(clocked, turn_service_for):
    provider = ScriptedProvider(StallsAfter("Question 9: what is"))

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.presses("go")
        await clock.advance(NEXT_BYTE_SECONDS - 0.1)

        assert derek.frames.streamed() == "Question 9: what is"
        assert "output.error" not in derek.frames.types()

        await clock.advance(0.1)

        assert derek.frames.last().type == "output.error"
        assert f"sent nothing for {NEXT_BYTE_SECONDS:g}s" in derek.frames.last().body["detail"]
        assert "output.text" not in derek.frames.types()
        assert provider.torn_down == 1

    _run(clocked, scenario)


def test_a_reply_is_waited_for_as_long_as_bytes_keep_coming(clocked, turn_service_for):
    words = ["one ", "two ", "three ", "four ", "five"]
    provider = ScriptedProvider(Trickles(words, gap=NEXT_BYTE_SECONDS - 0.1))

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, provider).open()
        await derek.presses("go")
        await clock.advance(len(words) * NEXT_BYTE_SECONDS)

        assert derek.frames.texts() == ["one two three four five"]
        assert "output.error" not in derek.frames.types()
        assert provider.torn_down == 0

    _run(clocked, scenario)


def test_a_cascade_answers_the_next_request_from_the_next_provider(clocked, turn_service_for):
    gemini = ScriptedProvider(Stalled())
    anthropic = ScriptedProvider(Reply("Question 9: ..."))
    cascade = AutoLiveLLMProvider([("gemini", gemini), ("anthropic", anthropic)])

    async def scenario(clock):
        derek = await _Conversation(turn_service_for, ai_service=AiService(cascade)).open()
        await derek.presses("go")
        await clock.advance(FIRST_BYTE_SECONDS)

        assert derek.frames.last().type == "output.error"
        assert anthropic.started == 0

        await derek.says("still there?")
        await clock.settle()

        assert derek.frames.texts() == ["Question 9: ..."]
        assert gemini.started == 1
        assert anthropic.started == 1

    _run(clocked, scenario)
