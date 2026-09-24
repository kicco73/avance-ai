from __future__ import annotations

import json
from datetime import datetime

import pytest

from ai import AiService
from ai.llm_provider import AIServiceProviderMalformedReplyError, AIServiceProviderOutputTruncatedError, LLMProvider
from ai.response_schema import StringField
from ai.turn.tracking_processor_ai import TrackingProcessorAfterAiMessage
from automaton.automaton import Action, Automaton, EnvKey, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts
from tracking.user_variables import UserVariables
from turn.turn_transaction import TurnTransaction

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

SUGGESTIONS = (
    '- **Reflejar**: "Veo que asiente, pero no termina de estar convencido."\n'
    '  *Por qué*: valida su "sí, sí" sin juzgarlo.\n\n'
    '- **Preguntar**: ¿Qué significa para usted empezar con la insulina?'
)


class StreamingProvider(LLMProvider):
    def __init__(self, reply: dict | str, chunk_size: int = 7, truncated: bool = False) -> None:
        super().__init__()
        self._raw = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        self._chunk_size = chunk_size
        self._truncated = truncated
        self.schemas: list[dict] = []

    async def stream_json(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self.schemas.append({name: field.json_schema() for name, field in schema.items()})
        for start in range(0, len(self._raw), self._chunk_size):
            yield self._raw[start:start + self._chunk_size]
        if self._truncated:
            raise AIServiceProviderOutputTruncatedError("length")

    def get_input_tokens(self, prompt: str) -> int:
        return len(prompt)


def _automaton() -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="talk")
    talk = State(
        input_processor="ai", key="talk", ui_label="Talk", final=False, contextual_prompt="Play the patient.",
        output=("conversacion_terminada", "sugerencias", "turnos"),
    )
    return Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "talk": talk},
        general_prompt="",
        signals=[],
        general_attachments=(),
        autotracking_on_ai_message=True,
        env_keys=[
            EnvKey(name="conversacion_terminada", type="bool", ai_definition="Whether the conversation is over."),
            EnvKey(name="sugerencias", type="string", ai_definition="Three suggestions, in Markdown."),
            EnvKey(name="turnos", type="number", ai_definition="How many turns so far."),
        ],
    )


def _processor(db, provider: LLMProvider) -> tuple[TrackingProcessorAfterAiMessage, PersistedEnv]:
    automaton = _automaton()
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username="user", project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(), start_state="talk", end_state="talk",
    )
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(
        env, MetricService(db, project_context), SessionFacts(db, project_context), UserFacts(db), db,
    )
    user_variables = UserVariables(
        automaton=automaton, state=automaton.states["talk"], project_id=PROJECT_ID, session_id=session_id,
    )
    processor = TrackingProcessorAfterAiMessage(
        AiService(auto_provider=provider), scope_builder, env, TurnTransaction(db, session_id, []), user_variables,
    )
    return processor, env


async def test_markdown_with_quotes_and_newlines_reaches_the_env_exactly_as_the_model_wrote_it(db):
    provider = StreamingProvider({
        "text": "Pues eso, que vengo porque me han dicho.",
        "output": {"conversacion_terminada": False, "sugerencias": SUGGESTIONS, "turnos": 3},
    })
    processor, env = _processor(db, provider)

    result = await processor.process("Buenos días")

    assert env.action_set() == {"conversacion_terminada": False, "sugerencias": SUGGESTIONS, "turnos": 3.0}
    assert result["reply"][0]["content"] == "Pues eso, que vengo porque me han dicho."


async def test_the_output_is_asked_for_as_an_object_of_typed_fields_nullable_except_a_flag(db):
    provider = StreamingProvider({"text": "Hola.", "output": {}})
    processor, _ = _processor(db, provider)

    await processor.process("Buenos días")

    assert provider.schemas[0]["output"]["properties"] == {
        "conversacion_terminada": {"type": "boolean"},
        "sugerencias": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "turnos": {"anyOf": [{"type": "number"}, {"type": "null"}]},
    }


async def test_a_field_the_model_sets_to_null_leaves_its_variable_unset_but_a_flag_reads_false(db):
    provider = StreamingProvider({
        "text": "Hola.", "output": {"conversacion_terminada": None, "sugerencias": SUGGESTIONS, "turnos": None},
    })
    processor, env = _processor(db, provider)

    await processor.process("Buenos días")

    assert env.action_set() == {"conversacion_terminada": False, "sugerencias": SUGGESTIONS}


async def test_a_value_sent_as_text_comes_back_in_the_type_the_field_declares(db):
    provider = StreamingProvider({
        "text": "Hola.", "output": {"conversacion_terminada": "true", "sugerencias": None, "turnos": "4"},
    })
    processor, env = _processor(db, provider)

    await processor.process("Buenos días")

    assert env.action_set() == {"conversacion_terminada": True, "turnos": 4.0}


async def test_an_output_that_is_not_an_object_sets_nothing_and_the_reply_still_arrives(db):
    provider = StreamingProvider({"text": "Hola.", "output": SUGGESTIONS})
    processor, env = _processor(db, provider)

    result = await processor.process("Buenos días")

    assert env.action_set() == {}
    assert result["reply"][0]["content"] == "Hola."


async def test_a_truncated_reply_with_text_is_an_error():
    provider = StreamingProvider({"text": "Hola, qué"}, truncated=True)

    with pytest.raises(AIServiceProviderOutputTruncatedError):
        async for _ in provider.generate_stream_with_schema("", [], {"text": StringField()}):
            pass


async def test_a_reply_that_ends_without_its_text_is_an_error_and_saves_nothing(db):
    provider = StreamingProvider('{"output": {"sugerencias": "- **Pedir permiso** antes de profundizar en ')
    processor, env = _processor(db, provider)

    with pytest.raises(AIServiceProviderMalformedReplyError):
        await processor.process("Buenos días")

    assert env.action_set() == {}


async def test_a_complete_reply_without_text_is_an_error():
    provider = StreamingProvider({"output": {"sugerencias": SUGGESTIONS}})

    with pytest.raises(AIServiceProviderMalformedReplyError):
        async for _ in provider.generate_stream_with_schema("", [], {"text": StringField(), "output": StringField()}):
            pass


async def test_a_truncated_reply_without_text_keeps_every_field_it_completed():
    provider = StreamingProvider({"title": "Sesión", "summary": "Corta"}, truncated=True)
    received: dict = {}

    async for _ in provider.generate_stream_with_schema(
        "", [], {"title": StringField(), "summary": StringField()}, on_metadata=received.__setitem__,
    ):
        pass

    assert received == {"title": "Sesión"}
