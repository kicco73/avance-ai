"""No exchange ends in silence.

Session 55 in production (2026-09-21): a choice was taken, the transition
was applied, and the reply's provider call failed with an error the
listener did not know by name. The exception left the task, no
`output.error` was published, and the person sat on a progress bubble
for 13 minutes. `publishing` (turn/outbound.py) is the seam every
exchange runs inside, and it now answers any exception that escapes with
`output.error` — for a choice taken and for a text answered alike — and
the listener goes on answering the next request.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import LLMProvider
from automaton.automaton import Action, Automaton, State
from system import bus
from system.bus import INPUT_BUTTON, INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

_PUBLISHED = (
	"output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
	"state.changed", "state.buttons", "output.error",
)


class _ProviderFailingOnce(LLMProvider):
	def __init__(self) -> None:
		super().__init__()
		self.calls = 0

	async def generate_stream_with_schema(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		self.calls += 1
		if self.calls == 1:
			raise RuntimeError("the provider went away")
		yield '{"text": "Back again."}'

	def get_input_tokens(self, prompt: str) -> int:
		return 0


def _automaton() -> Automaton:
	go = Action(name="go", ui_label="Go", ui_button="Go", target="b")
	init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
	states = {
		"": State(key="", ui_label="", final=False, actions=[init_action]),
		"a": State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[go]),
		"b": State(key="b", ui_label="B", final=False, contextual_prompt="welcome", actions=[], history_cutoff=True),
	}
	return Automaton(
		init_action=init_action, states=states, general_prompt="", signals=[], general_attachments=(),
		autotracking_on_ai_message=False, sources=[], project_id=PROJECT_ID,
	)


class _Recorder:
	def __init__(self) -> None:
		self.messages: list[Message] = []
		self.ended = asyncio.Event()

	async def take(self, message: Message) -> None:
		self.messages.append(message)
		if message.type in ("output.text", "output.error"):
			self.ended.set()

	async def next_exchange(self) -> list[Message]:
		self.ended.clear()
		await asyncio.wait_for(self.ended.wait(), timeout=10)
		return self.messages


async def _listening(turn_service_for, provider):
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), provider)
	bus._reset_for_tests()
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, 'live')
	recorder = _Recorder()
	for message_type in _PUBLISHED:
		bus.subscribe(message_type, recorder.take)
	TurnInput(turn_service, db).register()
	return session["id"], recorder


def _sent(type: str, session_id: int, **body) -> Message:
	return Message(type=type, body=body, username=WebSession().user, session_id=session_id, channel="webchat", origin_id="c1")


async def test_a_choice_whose_reply_fails_ends_with_output_error_and_the_next_request_is_answered(turn_service_for):
	provider = _ProviderFailingOnce()
	session_id, recorder = await _listening(turn_service_for, provider)

	await bus.publish(_sent(INPUT_BUTTON, session_id, id="go"))
	frames = await recorder.next_exchange()

	assert frames[-1].type == "output.error"
	assert frames[-1].body == {"message": "Unexpected server error.", "detail": "the provider went away"}
	assert "output.text" not in [m.type for m in frames]

	await bus.publish(_sent(INPUT_TEXT, session_id, text="still there?"))
	frames = await recorder.next_exchange()

	assert [m.body["text"] for m in frames if m.type == "output.text"] == ["Back again."]


async def test_a_text_whose_reply_fails_ends_with_output_error_the_same_way(turn_service_for):
	provider = _ProviderFailingOnce()
	session_id, recorder = await _listening(turn_service_for, provider)

	await bus.publish(_sent(INPUT_TEXT, session_id, text="hello"))
	frames = await recorder.next_exchange()

	assert frames[-1].type == "output.error"
	assert frames[-1].body["detail"] == "the provider went away"
