"""End-to-end: a `choice` button's current option text is queued for
translation the same way a manual button's `ui_button` is (through
`turn.translatable_labels`/`turn.translation`, see BUS.md), and the
translated text reaches the buttons a turn's caller gets back.
"""
from __future__ import annotations

import re

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from system import bus
from system.bus import TURN_TRANSLATION
from system.web_session import WebSession
from ai.turn.prompt import LangPrompt
from turn.sessions.env_for_session import env_for_session
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

_LABEL_RE = re.compile(r'- "([^"]+)": "([^"]+)"')


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


class UppercasingSchemaAiService:
	def is_provider_with_schema(self) -> bool:
		return True

	def get_models_info(self) -> dict:
		return {"auto": True, "current_index": 0, "models": []}

	async def generate_stream_with_metadata(
		self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
	):
		translations = {name: text.upper() for name, text in _LABEL_RE.findall(system_prompt.stable)}
		if translations:
			on_metadata("translations", translations)
			on_metadata("lang", {"src": "en-US", "dst": "it-IT"})
		yield "reply "


async def test_a_choice_options_current_text_is_translated_alongside_manual_buttons(turn_service_for):
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), ai_service=UppercasingSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["morning", "evening"]})

	result = await turn_service.process_turn(session["id"], "hello")

	by_name = {b["name"]: b for b in result["buttons"]}
	assert by_name["manual"]["ui_button"] == "MANUAL"
	assert by_name["choice:slot:0"]["ui_button"] == "MORNING"
	assert by_name["choice:slot:1"]["ui_button"] == "EVENING"


async def test_a_profiles_title_and_description_are_translated_and_its_key_is_not(turn_service_for):
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), ai_service=UppercasingSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": [
		{"title": "Ada", "picture_url": "/ada.png", "description": "The analyst.", "key": "ada"},
	]})

	result = await turn_service.process_turn(session["id"], "hello")

	button = {b["name"]: b for b in result["buttons"]}["choice:slot:0"]
	assert button["ui_button"] == "ADA"
	assert button["profile"] == {"title": "ADA", "picture_url": "/ada.png", "description": "THE ANALYST.", "key": "ada"}


def _automaton_reached_by_a_manual_action() -> Automaton:
	enter = Action(name="enter", ui_label="Enter", ui_button="Enter", target="b")
	state_a = State(input_processor="ai", key="a", ui_label="A", final=False, actions=[enter])
	state_b = State(input_processor="ai", key="b", ui_label="B", final=False, contextual_prompt="hi", choice_keys=("slot",))
	init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
	return Automaton(
		init_action=init_action,
		states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
		general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False,
		project_id=PROJECT_ID,
		env_keys=[EnvKey(name="slot", type="list", ai_definition="The appointment slot.")],
	)


async def test_a_manual_transitions_own_buttons_still_carry_the_translation_a_discarded_earlier_read_already_consumed(
	turn_service_for,
):
	"""apply_manual_action computes buttons_for twice for the same turn
	— once inside _process_turn_body (its result thrown away, see
	TurnService._messages_for_transition), once again for the response
	that actually reaches the caller. A read that consumes the per-
	session translation on the first, discarded call would leave the
	second with nothing to show but the untranslated option text."""
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton_reached_by_a_manual_action(), ai_service=UppercasingSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["morning"]})
	await turn_service.process_turn(session["id"], "hello")

	result = await turn_service.apply_manual_action("enter", session["id"])

	by_name = {b["name"]: b for b in result["buttons"]}
	assert by_name["choice:slot:0"]["ui_button"] == "MORNING"


async def test_buttons_stay_in_their_authored_language_until_the_user_has_written(turn_service_for):
	db = turn_service_for.db
	turn_service = turn_service_for(_automaton_reached_by_a_manual_action(), ai_service=UppercasingSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["mañana"]})

	result = await turn_service.apply_manual_action("enter", session["id"])

	by_name = {b["name"]: b for b in result["buttons"]}
	assert by_name["choice:slot:0"]["ui_button"] == "mañana"
	assert db.count_translations() == 0


async def test_no_translation_event_leaves_choice_buttons_with_the_raw_option_text(turn_service_for):
	class SilentSchemaAiService(UppercasingSchemaAiService):
		async def generate_stream_with_metadata(
			self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
		):
			yield "reply "

	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), ai_service=SilentSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["morning", "evening"]})

	result = await turn_service.process_turn(session["id"], "hello")

	by_name = {b["name"]: b for b in result["buttons"]}
	assert by_name["choice:slot:0"]["ui_button"] == "morning"
	assert by_name["choice:slot:1"]["ui_button"] == "evening"


async def test_turn_translation_events_carry_the_turns_own_lang_prompt_answer(turn_service_for):
	events: list = []

	async def take(message) -> None:
		events.append(message.body)

	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), ai_service=UppercasingSchemaAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["morning"]})

	bus.subscribe(TURN_TRANSLATION, take)
	try:
		await turn_service.process_turn(session["id"], "hello")
	finally:
		bus.unsubscribe(TURN_TRANSLATION, take)

	choice_event = next(e for e in events if e["key"] == "slot")
	assert (choice_event["src_lang"], choice_event["dst_lang"]) == ("en-US", "it-IT")


async def test_same_source_and_destination_language_writes_nothing_to_the_translation_cache(turn_service_for):
	class SameLanguageAiService(UppercasingSchemaAiService):
		async def generate_stream_with_metadata(
			self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
		):
			translations = {name: text for name, text in _LABEL_RE.findall(system_prompt.stable)}
			if translations:
				on_metadata("translations", translations)
				on_metadata("lang", {"src": "en-US", "dst": "en-US"})
			yield "reply "

	db = turn_service_for.db
	turn_service = turn_service_for(_automaton(), ai_service=SameLanguageAiService())
	db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, "live")
	env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": ["morning"]})

	await turn_service.process_turn(session["id"], "hello")

	assert db.count_translations() == 0


@pytest.mark.parametrize("raw", [
	{"src": "en", "dst": "it"},
	{"src": "en-US", "dst": "it"},
	{},
], ids=["bare-codes", "one-bare-code", "empty"])
def test_lang_prompt_rejects_anything_not_a_full_locale_tag(raw):
	assert LangPrompt().decode(raw) == ("", "")


def test_lang_prompt_accepts_a_full_locale_tag_pair():
	assert LangPrompt().decode({"src": "en-US", "dst": "it-IT"}) == ("en-US", "it-IT")
