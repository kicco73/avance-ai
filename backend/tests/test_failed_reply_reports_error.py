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

	async def stream_json(
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
		"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
		"a": State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[go]),
		"b": State(input_processor="ai", key="b", ui_label="B", final=False, contextual_prompt="welcome", actions=[], history_cutoff=True),
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
	turn_service_for.turn_service = turn_service
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


class _BlockingProvider(LLMProvider):
	def __init__(self) -> None:
		super().__init__()
		self.called = asyncio.Event()
		self.release = asyncio.Event()

	async def stream_json(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		self.called.set()
		await self.release.wait()
		yield '{"text": "Done."}'

	def get_input_tokens(self, prompt: str) -> int:
		return 0


class _SignalsThenCrash:
	"""An AiService-shaped fake for the autotracking-on-user-message path:
	the first generation reports a signal that fires the transition, the
	regeneration for the new state raises."""

	def __init__(self) -> None:
		self.calls = 0

	def get_models_info(self) -> dict:
		return {"auto": True, "current_index": 0, "models": []}

	def select_model(self, index: int | None) -> None:
		pass

	def is_provider_with_schema(self) -> bool:
		return True

	async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, **kwargs):
		self.calls += 1
		if self.calls == 2:
			raise RuntimeError("the provider went away")
		on_metadata("signals", {"mySignal": 1})
		yield "Hi!"


def _automaton_moving_on_a_signal(autotracking_on_ai_message: bool) -> Automaton:
	from automaton.automaton import Signal
	advance = Action(
		name="advance", ui_label="Advance", ui_button="", target="b",
		trigger="signal.mySignal >= 1", env={"steps": "1"},
	)
	init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
	states = {
		"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
		"a": State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[advance]),
		"b": State(input_processor="ai", key="b", ui_label="B", final=False, contextual_prompt="bye", actions=[], history_cutoff=True),
	}
	return Automaton(
		init_action=init_action, states=states, general_prompt="",
		signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")], general_attachments=(),
		autotracking_on_ai_message=autotracking_on_ai_message, sources=[], project_id=PROJECT_ID,
	)


async def test_a_choice_whose_reply_failed_left_the_state_where_it_was_and_can_be_taken_again(turn_service_for):
	provider = _ProviderFailingOnce()
	session_id, recorder = await _listening(turn_service_for, provider)

	await bus.publish(_sent(INPUT_BUTTON, session_id, id="go"))
	frames = await recorder.next_exchange()
	assert frames[-1].type == "output.error"
	assert "state.changed" not in [m.type for m in frames]

	await bus.publish(_sent(INPUT_BUTTON, session_id, id="go"))
	frames = await recorder.next_exchange()

	moved = [m.body for m in frames if m.type == "state.changed"]
	assert [(m["from_state"], m["new_state"]) for m in moved] == [("a", "b")]
	assert [m.body["text"] for m in frames if m.type == "output.text"] == ["Back again."]


async def test_a_text_whose_reply_failed_left_no_row_and_its_resend_is_answered_once(turn_service_for):
	provider = _ProviderFailingOnce()
	session_id, recorder = await _listening(turn_service_for, provider)
	turn_service = turn_service_for.turn_service

	await bus.publish(_sent(INPUT_TEXT, session_id, text="hello"))
	await recorder.next_exchange()

	assert turn_service.read_history(session_id) == []

	await bus.publish(_sent(INPUT_TEXT, session_id, text="hello"))
	await recorder.next_exchange()

	history = turn_service_for.db.get_turn_history(session_id, None, None)
	assert [(m["role"], m["content"]) for m in history] == [("user", "hello"), ("assistant", "Back again.")]
	assert history[0]["answered_by"] == history[1]["id"]


@pytest.mark.parametrize("autotracking_on_ai_message", [False, True])
async def test_a_transition_decided_by_a_turn_whose_reply_failed_is_not_kept(turn_service_for, autotracking_on_ai_message):
	ai_service = _SignalsThenCrash()
	turn_service = turn_service_for(_automaton_moving_on_a_signal(autotracking_on_ai_message), ai_service=ai_service)
	db = turn_service_for.db
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, 'live')
	session_id = session["id"]
	if autotracking_on_ai_message:
		ai_service.calls = 1

	with pytest.raises(RuntimeError, match="the provider went away"):
		await turn_service.process_turn(session_id, "hello")

	assert turn_service_for.project_service.get_automaton_and_state_for_session(session_id)[1].key == "a"
	assert db.get_current_state_for_session(session_id) == "a"
	assert turn_service.get_env(session_id)["action_set"].get("steps") is None
	assert turn_service.read_history(session_id) == []
	assert [row for row in db.get_signals(session_id) if row["new_state"] == "b"] == []


async def test_a_message_being_answered_is_in_the_transcript_while_the_reply_is_written(turn_service_for):
	provider = _BlockingProvider()
	turn_service = turn_service_for(_automaton(), provider)
	db = turn_service_for.db
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, 'live')
	session_id = session["id"]

	turn = asyncio.create_task(turn_service.process_turn(session_id, "are you there?"))
	await asyncio.wait_for(provider.called.wait(), timeout=5)

	in_flight = turn_service.read_history(session_id)
	assert [(m["role"], m["content"], m["id"]) for m in in_flight] == [("user", "are you there?", None)]
	assert db.get_messages(session_id) == []

	provider.release.set()
	await asyncio.wait_for(turn, timeout=5)

	landed = turn_service.read_history(session_id)
	assert [(m["role"], m["content"]) for m in landed] == [("user", "are you there?"), ("assistant", "Done.")]
	assert all(m["id"] is not None for m in landed)
