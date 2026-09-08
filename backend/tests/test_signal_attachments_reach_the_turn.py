"""A signal's own `attachments:` must accompany the turn that carries its
`definition` — the turn that actually requests 'signals' and could
trigger from it (see PROJECT_SPECS.md §3.1/§6 and
tracking.tracking_processor._turn_attachment_paths). Before this fix they
were validated at build time and then never loaded into any turn.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from tracking.attachments import load_attachments
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.project_files import PROJECT_FILE_CACHE, project_files_for
from tracking.session_facts import SessionFacts
from tracking.tracking_processor import UserVariables, _turn_attachment_paths, estimate_state_prompt
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"

FILES = {
    "global.txt": b"global note\n",
    "mood.txt": b"mood instructions\n",
    "other.txt": b"other instructions\n",
}
CONTENT_TYPES = {name: "text/plain" for name in FILES}


def _seed_files(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, FILES, CONTENT_TYPES)
    PROJECT_FILE_CACHE.forget_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.get_project_published_revision(PROJECT_ID)


def _automaton(revision: int) -> Automaton:
    """State "a" can only ever trigger from signal "mood" (its one
    action's trigger references nothing else); signal "other" is
    declared but triggerable from nowhere in this automaton. State "a"
    also redeclares the global attachment on itself, to exercise dedup.
    """
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.", attachments=("mood.txt",))
    other = Signal(name="other", ui_label="Other", definition="unrelated.", attachments=("other.txt",))
    advance = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50",
    )
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[advance],
        attachments=("global.txt",),
    )
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="", signals=[mood, other], general_attachments=("global.txt",),
        autotracking_on_ai_message=False, project_id=PROJECT_ID,
    )
    automaton.set_storage_location(revision)
    return automaton


def test_a_triggerable_signals_attachments_are_included_and_deduplicated():
    automaton = _automaton(revision=1)
    state_a = automaton.states["a"]

    # global, then state's own (a duplicate of the global one, dropped),
    # then mood's — never other's, nothing in "a" can trigger it.
    assert _turn_attachment_paths(automaton, state_a, include_signal_attachments=True) == [
        "global.txt", "mood.txt",
    ]


def test_the_gate_excludes_signal_attachments_when_signals_are_not_requested():
    automaton = _automaton(revision=1)
    state_a = automaton.states["a"]

    assert _turn_attachment_paths(automaton, state_a, include_signal_attachments=False) == ["global.txt"]


def test_a_state_that_triggers_from_no_signal_sends_no_signal_attachments():
    automaton = _automaton(revision=1)
    state_b = automaton.states["b"]

    assert _turn_attachment_paths(automaton, state_b, include_signal_attachments=True) == ["global.txt"]


class RecordingSchemaAiService:
    """Schema-capable fake that records the schema and history passed to
    generate_stream_with_metadata on every call, firing a
    transition-triggering signals value on the first (optimistic) call
    only — same shape as test_regeneration_skips_signals.py's own fake,
    plus recording `history` (the priming attachments live there, not in
    the system prompt)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.histories: list[list[dict]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append(dict(schema))
        self.histories.append(history)
        if len(self.calls) == 1:
            on_metadata("signals", '{"mood": 80}')
            yield "draft "
        else:
            yield "final "


def _processor(db, automaton: Automaton, session_id: int, ai_service: RecordingSchemaAiService) -> TrackingProcessorAfterUserMessage:
    project_service = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_service)
    env = PersistedEnv(db, project_service, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_service), UserFacts(db), db)
    user_variables = UserVariables(
        automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id,
    )
    return TrackingProcessorAfterUserMessage(ai_service, scope_builder, env, db, user_variables)


async def test_the_detecting_call_carries_the_triggerable_signals_own_attachment_and_regeneration_does_not(db):
    revision = _seed_files(db)
    automaton = _automaton(revision)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=revision,
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    ai_service = RecordingSchemaAiService()
    processor = _processor(db, automaton, session_id, ai_service)

    await processor.process("hello")

    assert len(ai_service.histories) == 2, "expected an optimistic (detecting) call plus one regeneration"

    detecting_blocks = ai_service.histories[0][0]["content"]
    assert [block["filename"] for block in detecting_blocks] == ["global.txt", "mood.txt"]

    # The regeneration call (post-transition, state "b") never re-requests
    # 'signals' (see test_regeneration_skips_signals.py) — its own turn
    # attachments must not carry mood's file either.
    regeneration_blocks = ai_service.histories[1][0]["content"]
    assert [block["filename"] for block in regeneration_blocks] == ["global.txt"]


async def test_estimate_state_prompt_counts_the_same_attachment_bytes_the_real_turn_sends(db):
    revision = _seed_files(db)
    automaton = _automaton(revision)
    state_a = automaton.states["a"]
    files = project_files_for(db, automaton)

    real_turn_archives = load_attachments(files, _turn_attachment_paths(automaton, state_a, include_signal_attachments=True))
    assert [a.filename for a in real_turn_archives] == ["global.txt", "mood.txt"]

    estimate_text = estimate_state_prompt(None, automaton, state_a, files)

    for archive in real_turn_archives:
        assert f"[Attachment: {archive.filename}]\n{archive.source['data']}" in estimate_text
