"""BatchLiteSignalSource is BatchSignalSource with only its transcript-
building hooks overridden (_tag_instructions/_transcript_role/
_anchor_message_id) — these tests drive prepare_batch() and read the
transcript back out of the one system prompt it actually sends, pinning
down exactly which messages and labels reach the model under each
autotracking_on_ai_message mode. BatchSignalSource's own transcript is
covered here too, as a regression guard proving the refactor didn't
change its output."""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from testing.signal_sources import BATCH_LITE_TAG_INSTRUCTIONS_TEMPLATE, BatchLiteSignalSource, BatchSignalSource
from tracking.env import Env

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


class _RecordingAiService:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_snapshot(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(
        self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
    ):
        self.system_prompts.append(system_prompt.full_text())
        yield ""


def _automaton(autotracking_on_ai_message: bool) -> Automaton:
    init_action = Action(name="init", ui_label="init", ui_button="", target="")
    return Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="general",
        signals=[Signal(name="mood", ui_label="Mood", definition="whatever")],
        general_attachments={},
        autotracking_on_ai_message=autotracking_on_ai_message,
    )


def _session_id(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), start_state="",
    )


def _seed_conversation(db, session_id: int) -> list[int]:
    """Three user/assistant exchanges — returns the three user message ids,
    the same "turn_ids" TestReplayJob would hand to prepare_batch()."""
    user_ids = []
    for i in range(3):
        user_ids.append(db.save_message("user", f"user says {i}", session_id))
        db.save_message("assistant", f"assistant replies {i}", session_id)
    return user_ids


def _source(cls, db, automaton, session_id: int, ai_service):
    return cls(
        ai_service=ai_service, tracking_service=None, db=db, automaton=automaton, session_id=session_id,
        env=Env(), messages=db.get_messages(session_id),
    )


async def _prompt_sent_for(cls, db, automaton, session_id: int, turn_ids: list[int]) -> str:
    ai_service = _RecordingAiService()
    await _source(cls, db, automaton, session_id, ai_service).prepare_batch(turn_ids)
    assert len(ai_service.system_prompts) == 1
    return ai_service.system_prompts[0]


def _transcript_in(prompt: str) -> str:
    return prompt.split("Conversation transcript:\n")[1].split("\n\n")[0]


async def test_batch_signal_source_keeps_both_roles_labeled_at_the_user_message(db):
    automaton = _automaton(autotracking_on_ai_message=False)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(BatchSignalSource, db, automaton, session_id, turn_ids)
    assert _transcript_in(prompt) == (
        "[Turn 1]\n"
        "User: user says 0\n"
        "Assistant: assistant replies 0\n"
        "[Turn 2]\n"
        "User: user says 1\n"
        "Assistant: assistant replies 1\n"
        "[Turn 3]\n"
        "User: user says 2"
    )


async def test_batch_lite_keeps_only_user_messages_when_tracking_before_ai_reply(db):
    automaton = _automaton(autotracking_on_ai_message=False)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(BatchLiteSignalSource, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt) == (
        "[Turn 1]\nUser: user says 0\n"
        "[Turn 2]\nUser: user says 1\n"
        "[Turn 3]\nUser: user says 2"
    )
    assert BATCH_LITE_TAG_INSTRUCTIONS_TEMPLATE.format(shown_role="user", other_role="assistant") in prompt


async def test_batch_lite_keeps_only_assistant_messages_when_tracking_after_ai_reply(db):
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(BatchLiteSignalSource, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt) == (
        "[Turn 1]\nAssistant: assistant replies 0\n"
        "[Turn 2]\nAssistant: assistant replies 1\n"
        "[Turn 3]\nAssistant: assistant replies 2"
    )
    assert BATCH_LITE_TAG_INSTRUCTIONS_TEMPLATE.format(shown_role="assistant", other_role="user") in prompt


async def test_batch_lite_skips_the_label_for_a_turn_with_no_assistant_reply_yet(db):
    """The trailing user message of an in-progress session has no
    assistant reply to anchor on in "after AI message" mode — that turn's
    label is dropped rather than crashing or mislabeling another line."""
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)
    trailing_user_id = db.save_message("user", "user says 3, unanswered", session_id)
    turn_ids = turn_ids + [trailing_user_id]

    prompt = await _prompt_sent_for(BatchLiteSignalSource, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt) == (
        "[Turn 1]\nAssistant: assistant replies 0\n"
        "[Turn 2]\nAssistant: assistant replies 1\n"
        "[Turn 3]\nAssistant: assistant replies 2"
    )
