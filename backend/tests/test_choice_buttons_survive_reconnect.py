"""A dropped and resumed connection re-enters the same session
(`session.enter` with the session's own id — see BUS.md's "Whose
conversation it is"), which re-announces `state.buttons` from scratch.
The choice buttons it offers must survive that — nothing about losing
and regaining a connection should make an option disappear.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from system import bus
from system.bus import SESSION_ENTER, STATE_BUTTONS, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn.sessions.env_for_session import env_for_session
from turn_harness import PROJECT_ID, drive_turn, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression


class _FakeProvider:
	async def generate_stream_with_schema(
		self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
	):
		yield '{"text": "Hi."}'

	def get_total_tokens(self) -> int:
		return 0

	def get_input_tokens(self, prompt: str) -> int:
		return 0

	def get_max_output_tokens(self) -> int:
		return 4096


def _automaton() -> Automaton:
	manual = Action(name="manual", ui_label="Manual", ui_button="Manual", target="a")
	init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
	state_a = State(
		input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[manual], choice_keys=("slot",),
	)
	return Automaton(
		init_action=init_action,
		states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
		general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False,
		project_id=PROJECT_ID,
		env_keys=[EnvKey(name="slot", type="list", ai_definition="The appointment slot.")],
	)


async def _reenter(turn_service, db, session_id: int, timeout: float = 10) -> list:
	frames = []
	finished = asyncio.Event()

	async def take(message) -> None:
		if message.session_id != session_id:
			return
		frames.append(message)
		if message.type == STATE_BUTTONS:
			finished.set()

	bus.subscribe(STATE_BUTTONS, take)
	if not any(getattr(listener, "__self__", None).__class__ is TurnInput for listener in bus.handlers_for(SESSION_ENTER)):
		TurnInput(turn_service, db).register()
	try:
		await bus.publish(Message(
			type=SESSION_ENTER, body={"session_type": "live"}, username=WebSession().user,
			session_id=session_id, channel="webchat", origin_id="connection-2",
		))
		await asyncio.wait_for(finished.wait(), timeout=timeout)
	finally:
		bus.unsubscribe(STATE_BUTTONS, take)
	return frames


async def test_reentering_the_same_session_still_offers_the_choice_buttons_a_turn_showed(turn_service_for):
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), _FakeProvider())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	session_id = session["id"]
	env_for_session(db, db.get_chat_session(session_id)).update_action_set({"slot": ["morning", "evening"]})

	await drive_turn(turn_service, db, session_id, "connection-1", "hello")

	frames = await _reenter(turn_service, db, session_id)

	buttons = next(f.body["actions"] for f in frames if f.type == STATE_BUTTONS)
	names = [b["name"] for b in buttons]
	assert "choice:slot:0" in names and "choice:slot:1" in names
