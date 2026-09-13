"""End-to-end: a turn's own resulting state's manual actions get their
ui_button translated via the TranslatePrompt composed as the turn's last
channel, and the translation reaches the final state payload the caller
gets back.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from automaton.payloads import pressable_actions
from system.web_session import WebSession
from tracking.tracking_processor import TrackingProcessor
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a pytest fixture, used by name

REACHES_INTO = {
    "_button_labels_to_translate": "pins this filter to the public pressable_actions it duplicates and could drift from",
}

pytestmark = pytest.mark.regression


def _automaton_showing(action: Action) -> Automaton:
	state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
	state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
	init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
	return Automaton(
		init_action=init_action,
		states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
		general_prompt="",
		signals=[],
		general_attachments={},
		autotracking_on_ai_message=False,
		project_id=PROJECT_ID,
	)


@pytest.mark.parametrize(("action", "auto_tracking_enabled"), [
	(Action(name="skip", ui_label="Skip", ui_button="", target="a"), True),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50"), True),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50"), False),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b"), True),
], ids=["no-ui-button", "triggerable-auto-on", "triggerable-auto-off", "untriggered"])
def test_the_buttons_queued_for_translation_are_exactly_the_ones_the_state_shows(action, auto_tracking_enabled):
	"""Two pieces of production code decide the same thing and must never
	disagree: `pressable_actions` is what the caller is offered, and
	`_button_labels_to_translate` is what gets translated — the second is
	the first, minus whatever has nothing to translate. A second call site
	already passes a hardcoded auto_tracking_enabled=False (see
	tracking_processor.py's own prompt-size estimate), so the two can drift
	apart without either failing on its own."""
	automaton = _automaton_showing(action)
	payload = automaton.get_state_payload(automaton.states["a"])

	queued = TrackingProcessor._button_labels_to_translate(
		automaton.states["a"], auto_tracking_enabled=auto_tracking_enabled,
	)

	assert queued == {
		a["name"]: a["ui_button"]
		for a in pressable_actions(payload["actions"], auto_tracking_enabled) if a["ui_button"]
	}


class RecordingSchemaAiService:
	def __init__(self, translations_json: str | None) -> None:
		self._translations_json = translations_json
		self.calls: list[dict[str, str]] = []

	def is_provider_with_schema(self) -> bool:
		return True

	def get_models_info(self) -> dict:
		return {"auto": True, "current_index": 0, "models": []}

	async def generate_stream_with_metadata(
		self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
	):
		self.calls.append(dict(schema))
		if self._translations_json is not None:
			on_metadata("translations", self._translations_json)
		yield "reply "


@pytest.mark.parametrize(("translations_json", "expected_button"), [
	('{"advance": "Avanti"}', "Avanti"),
	("not json", "Advance"),
], ids=["translated", "malformed-falls-back"])
async def test_a_state_with_a_manual_action_requests_translations_and_the_result_reaches_the_final_state_payload(
	turn_service_for, translations_json, expected_button,
):
	ai_service = RecordingSchemaAiService(translations_json=translations_json)
	automaton = _automaton_showing(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b"))
	turn_service = turn_service_for(automaton, ai_service=ai_service)
	turn_service_for.db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
	session = await turn_service.enter_session(PROJECT_ID, 'live')

	result = await turn_service.process_turn(session["id"], "hello")

	assert "translations" in ai_service.calls[0]
	action = next(a for a in result["state"]["actions"] if a["name"] == "advance")
	assert action["ui_button"] == expected_button
