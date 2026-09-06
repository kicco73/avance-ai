"""End-to-end: a turn's own resulting state's manual actions get their
ui_button translated via the TranslatePrompt composed as the turn's last
channel, and the translation reaches the final state payload the caller
gets back — see TrackingProcessor._button_labels_to_translate/
_append_translate_prompt/_current_state_payload.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_processor import TrackingProcessor, UserVariables
from tracking.user_facts import UserFacts
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _state_with(action: Action) -> State:
	return State(key="a", ui_label="A", final=False, actions=[action])


@pytest.mark.parametrize(("action", "auto_tracking_enabled", "expected"), [
	(Action(name="skip", ui_label="Skip", ui_button="", target="a"), True, {}),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50"), True, {}),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50"), False, {"advance": "Advance"}),
	(Action(name="advance", ui_label="Advance", ui_button="Advance", target="b"), True, {"advance": "Advance"}),
], ids=["no-ui-button", "triggerable-auto-on", "triggerable-auto-off", "untriggered"])
def test_only_the_buttons_a_state_actually_shows_are_queued_for_translation(action, auto_tracking_enabled, expected):
	"""A test/manual session shows every action as a button regardless of
	trigger (see automaton.manual_actions_for) — translation must follow."""
	assert TrackingProcessor._button_labels_to_translate(_state_with(action), auto_tracking_enabled=auto_tracking_enabled) == expected


def _automaton() -> Automaton:
	manual_action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b")
	state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[manual_action])
	state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
	init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
	return Automaton(
		init_action=init_action,
		states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
		general_prompt="",
		signals=[],
		attachments={},
		general_attachments={},
		autotracking_on_ai_message=False,
	)


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


def _processor(db, ai_service) -> TrackingProcessorAfterUserMessage:
	db.ensure_project(PROJECT_ID)
	db.publish_project(PROJECT_ID)
	session_id = db.create_chat_session(
		username=USERNAME, project_id=PROJECT_ID,
		revision=db.get_project_published_revision(PROJECT_ID),
		datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
		start_state="a", end_state="a",
	)
	automaton = _automaton()
	project_context = FixedProjectContext(project_id=PROJECT_ID)
	metrics = MetricService(db, project_context)
	env = PersistedEnv(db, project_context, session_id)
	scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
	user_variables = UserVariables(
		automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id,
	)
	return TrackingProcessorAfterUserMessage(ai_service, scope_builder, env, db, user_variables)


@pytest.mark.parametrize(("translations_json", "expected_button"), [
	('{"advance": "Avanti"}', "Avanti"),
	("not json", "Advance"),
], ids=["translated", "malformed-falls-back"])
async def test_a_state_with_a_manual_action_requests_translations_and_the_result_reaches_the_final_state_payload(db, translations_json, expected_button):
	ai_service = RecordingSchemaAiService(translations_json=translations_json)
	processor = _processor(db, ai_service)

	result = await processor.process("hello")

	assert "translations" in ai_service.calls[0]
	action = next(a for a in result["state"]["actions"] if a["name"] == "advance")
	assert action["ui_button"] == expected_button
