"""End-to-end: a turn's own resulting state's manual actions get their
ui_button translated via the TranslateChannel appended as the turn's last
channel, and the translation reaches the final state payload the caller
gets back — see TrackingProcessor._button_labels_to_translate/
_append_translate_channel/_current_state_payload.
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


# --- TrackingProcessor._button_labels_to_translate: pure filter logic ---

def test_excludes_an_action_with_an_empty_ui_button():
	action = Action(name="skip", ui_label="Skip", ui_button="", target="a")
	state = State(key="a", ui_label="A", final=False, actions=[action])
	assert TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=True) == {}


def test_excludes_a_triggerable_action_when_auto_tracking_is_on():
	action = Action(
		name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50",
	)
	state = State(key="a", ui_label="A", final=False, actions=[action])
	assert TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=True) == {}


def test_includes_a_triggerable_action_when_auto_tracking_is_off():
	"""A test/manual session shows every action as a button regardless of
	trigger (see automaton.manual_actions_for) — translation must follow."""
	action = Action(
		name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50",
	)
	state = State(key="a", ui_label="A", final=False, actions=[action])
	assert TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=False) == {"advance": "Advance"}


def test_includes_an_untriggered_action_regardless_of_auto_tracking():
	action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b")
	state = State(key="a", ui_label="A", final=False, actions=[action])
	assert TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=True) == {"advance": "Advance"}


# --- End-to-end through a real turn ---

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


async def test_a_state_with_a_manual_action_requests_translations(db):
	ai_service = RecordingSchemaAiService(translations_json=None)
	processor = _processor(db, ai_service)

	await processor.process("hello")

	assert "translations" in ai_service.calls[0]


async def test_the_translated_button_label_reaches_the_final_state_payload(db):
	ai_service = RecordingSchemaAiService(translations_json='{"advance": "Avanti"}')
	processor = _processor(db, ai_service)

	result = await processor.process("hello")

	action = next(a for a in result["state"]["actions"] if a["name"] == "advance")
	assert action["ui_button"] == "Avanti"


async def test_a_malformed_translations_response_falls_back_to_the_original_label(db):
	ai_service = RecordingSchemaAiService(translations_json="not json")
	processor = _processor(db, ai_service)

	result = await processor.process("hello")

	action = next(a for a in result["state"]["actions"] if a["name"] == "advance")
	assert action["ui_button"] == "Advance"
