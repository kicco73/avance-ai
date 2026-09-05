"""The explicit automaton/model binding, end to end through a real
TrackingProcessor turn: the reply's `memory` field is the model's own
notes (an `env` field in a reply is ignored); the automaton's env reaches
the model only as the trailing env block of a state that reads an
avance:env source, and only through `update` on one it may write; a
must-read on the env source forces its `select` and never its `update`;
an `update` made during an optimistic reply that a transition then
discards stays written and is what the regeneration sees.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Signal, Source, State
from db.models import Tracking
from metrics.metric_service import MetricService
from db.db import Db
from tracking.env import PersistedEnv
from tracking.env_prompt_block import ENV_BLOCK_HEADER
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_processor import UserVariables
from tracking.tracking_processor_ai import TrackingProcessorAfterAiMessage
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"

ENV_SOURCE = Source(name="env", url="avance:env", ui_label="Env", ai_definition="The variables of this case.")
ENV_KEYS = [
    EnvKey(name="flight", ai_access="readwrite", ai_definition="The flight code."),
    EnvKey(name="pnr", ai_access="readwrite", ai_definition="The record locator."),
    EnvKey(name="customer_email", ai_access="readonly", ai_definition="The customer's email."),
    EnvKey(name="_flight_record"),
]


def _automaton(state_a: State, state_b: State | None = None, *, with_env: bool = True, after_ai: bool = False) -> Automaton:
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a}
    if state_b is not None:
        states["b"] = state_b
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[mood], attachments={},
        general_attachments={}, autotracking_on_ai_message=after_ai,
        env_keys=ENV_KEYS if with_env else [], sources=[ENV_SOURCE] if with_env else [],
    )


class RecordingAiService:
    """Records every generate call's prompt and tool-calling kwargs; on the
    first call, optionally performs one tool call and reports metadata."""

    def __init__(self, metadata_per_call: list[dict] | None = None, tool_call: tuple[str, dict] | None = None) -> None:
        self._metadata_per_call = metadata_per_call or [{}]
        self._tool_call = tool_call
        self.prompts: list[str] = []
        self.tool_sets: list = []
        self.forced: list[bool] = []
        self.tool_results: list[str] = []

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(
        self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False, tool_abort=None,
    ):
        call_index = len(self.prompts)
        self.prompts.append(system_prompt)
        self.tool_sets.append(tool_set)
        self.forced.append(force_required_tools)
        if self._tool_call is not None and tool_set is not None and call_index == 0:
            name, arguments = self._tool_call
            result = await tool_set.call(name, arguments)
            self.tool_results.append(result)
            on_metadata("tool_result", {"name": name, "arguments": arguments, "result": result, "summary_text": ""})
        metadata = self._metadata_per_call[min(call_index, len(self._metadata_per_call) - 1)]
        for key, value in metadata.items():
            on_metadata(key, value)
        yield "Hi!"


@pytest.fixture
def file_db(tmp_path) -> Db:
    # File-backed, not :memory: — a tool call runs its driver via
    # ToolSet.call's own asyncio.to_thread, and a second thread's own
    # connection to ":memory:" would see a distinct, empty database (see
    # test_tool_set.py's own file_db).
    return Db(f"sqlite:///{tmp_path / 'binding.db'}")


def _session(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(), start_state="a", end_state="a",
    )


def _processor(db, automaton: Automaton, ai_service, session_id: int, processor_cls=TrackingProcessorAfterUserMessage):
    project_service = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, project_service, session_id)
    scope_builder = EvaluationScopeBuilder(
        env, MetricService(db, project_service), SessionFacts(db, project_service), UserFacts(db), db,
    )
    user = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    return processor_cls(ai_service, scope_builder, env, db, user), env


READS_ENV = State(key="a", ui_label="A", final=True, contextual_prompt="You are in A.", ai_may_read_sources=("env",))
NO_TOOLS = State(key="a", ui_label="A", final=True, contextual_prompt="You are in A.")


async def test_a_memory_field_in_the_reply_is_stored_and_an_env_field_is_ignored(db):
    session_id = _session(db)
    ai_service = RecordingAiService([{"memory": "goal: quit", "env": "goal: forged\npnr: forged"}])
    processor, env = _processor(db, _automaton(NO_TOOLS, with_env=False), ai_service, session_id)

    await processor.process("hello")

    assert env.memory() == {"goal": "quit"}
    assert env.action_set() == {}


async def test_the_prompt_of_a_state_reading_the_env_source_ends_with_the_env_block_and_keeps_memory_separate(db):
    session_id = _session(db)
    ai_service = RecordingAiService()
    processor, env = _processor(db, _automaton(READS_ENV), ai_service, session_id)
    env.update({"goal": "quit"})
    env.update_action_set({"flight": "VY3003", "_flight_record": "secret", "customer_email": "a@b.c"})

    await processor.process("hello")

    prompt = ai_service.prompts[0].full_text()
    block = prompt[prompt.index(ENV_BLOCK_HEADER):]
    assert block == f"{ENV_BLOCK_HEADER}\nflight: VY3003\npnr: \ncustomer_email: a@b.c"
    assert "secret" not in prompt
    assert "Current memory:" in prompt and "goal: quit" in prompt
    # Memory/env are the volatile tail now (see SystemPrompt) — both land
    # after the stable prefix's own schema-order instructions.
    assert prompt.index("filling in its fields") < prompt.index("goal: quit") < prompt.index(ENV_BLOCK_HEADER)


async def test_a_state_not_reading_the_env_source_gets_no_env_block_but_still_its_memory(db):
    session_id = _session(db)
    ai_service = RecordingAiService()
    processor, env = _processor(db, _automaton(NO_TOOLS), ai_service, session_id)
    env.update({"goal": "quit"})
    env.update_action_set({"flight": "VY3003"})

    await processor.process("hello")

    prompt = ai_service.prompts[0].full_text()
    assert ENV_BLOCK_HEADER not in prompt and "VY3003" not in prompt
    assert "goal: quit" in prompt
    assert ai_service.tool_sets == [None]


async def test_hello_world_sends_no_tools_and_no_env_block(db):
    session_id = _session(db)
    ai_service = RecordingAiService()
    processor, _ = _processor(db, _automaton(NO_TOOLS, with_env=False), ai_service, session_id)

    await processor.process("hello")

    assert ai_service.tool_sets == [None]
    assert ENV_BLOCK_HEADER not in ai_service.prompts[0].full_text()
    assert "Current memory:" in ai_service.prompts[0].full_text()


async def test_a_must_read_on_the_env_source_forces_its_select_and_never_its_update(db):
    session_id = _session(db)
    state = State(
        key="a", ui_label="A", final=True, contextual_prompt="You are in A.",
        ai_must_read_sources=("env",), ai_may_write_sources=("env",),
    )
    ai_service = RecordingAiService()
    processor, _ = _processor(db, _automaton(state), ai_service, session_id)

    await processor.process("hello")

    tool_set = ai_service.tool_sets[0]
    assert ai_service.forced == [True]
    assert {spec.name for spec in tool_set.specs()} == {"source_env_select", "source_env_update"}
    assert {spec.name for spec in tool_set.required_specs()} == {"source_env_select"}


async def test_an_env_update_during_the_discarded_optimistic_reply_survives_into_the_regeneration(file_db):
    """signal-tracking-on-ai-message: false — the reply is generated first
    and regenerated once a signal fires a transition. An `update` the
    model made during the discarded reply is already persisted when the
    regeneration starts, is never rolled back, and the regeneration (in
    the new state, which reads the env) sees it as the current value."""
    session_id = _session(file_db)
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="You are in A.",
        actions=[Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50")],
        ai_may_read_sources=("env",), ai_may_write_sources=("env",),
    )
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.", ai_may_read_sources=("env",))
    ai_service = RecordingAiService(
        [{"signals": '{"mood": 80}'}, {}],
        tool_call=("source_env_update", {"values": [], "fields": {"pnr": "ABC123"}}),
    )
    processor, env = _processor(file_db, _automaton(state_a, state_b), ai_service, session_id)

    result = await processor.process("my locator is ABC123")

    assert ai_service.tool_results == ["1 row updated"]
    assert len(ai_service.prompts) == 2 and "pnr: ABC123" in ai_service.prompts[1].full_text()
    assert env.action_set()["pnr"] == "ABC123"
    assert result["state"]["key"] == "b"
    tool_row = Tracking.get((Tracking.origin == "tool") & Tracking.action_env.is_null(False))
    assert tool_row.message_id == result["assistant_message_id"]


async def test_the_after_ai_message_processor_binds_the_update_row_to_its_assistant_message_too(file_db):
    session_id = _session(file_db)
    state = State(
        key="a", ui_label="A", final=True, contextual_prompt="You are in A.",
        ai_may_read_sources=("env",), ai_may_write_sources=("env",),
    )
    ai_service = RecordingAiService(
        [{"memory": "note: x"}], tool_call=("source_env_update", {"values": [], "fields": {"flight": "VY3003"}}),
    )
    processor, env = _processor(
        file_db, _automaton(state, after_ai=True), ai_service, session_id, processor_cls=TrackingProcessorAfterAiMessage,
    )

    result = await processor.process("it's VY3003")

    assert env.action_set()["flight"] == "VY3003"
    assert env.memory() == {"note": "x"}
    tool_row = Tracking.get((Tracking.origin == "tool") & Tracking.action_env.is_null(False))
    assert tool_row.message_id == result["assistant_message_id"]
    # The bookkeeping rows never masquerade as the message's evaluation point.
    assert file_db.get_signal_row_by_message(result["assistant_message_id"]) is None
