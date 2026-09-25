from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from testing.signal_sources import (
    ASSISTANT_EXCERPT_CHARS, BATCH_LITE_TAG_INSTRUCTIONS, TURN_HORIZON_INSTRUCTIONS, BatchLiteSignalSource,
    BatchSignalSource,
)
from tracking.env import Env

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


class _RecordingAiService:
    def __init__(self, signals_csv: str | None = None) -> None:
        self.system_prompts: list[str] = []
        self._signals_csv = signals_csv

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_snapshot(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(
        self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
    ):
        self.system_prompts.append(system_prompt.full_text())
        if self._signals_csv is not None:
            on_metadata("signals", self._signals_csv)
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


def _seed_conversation(db, session_id: int, assistant_replies=None) -> list[int]:
    replies = assistant_replies or [f"assistant replies {i}" for i in range(3)]
    user_ids = []
    for i, reply in enumerate(replies):
        user_ids.append(db.save_message("user", f"user says {i}", session_id))
        db.save_message("assistant", reply, session_id)
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


@pytest.mark.parametrize("autotracking_on_ai_message", [False, True])
async def test_batch_lite_shows_both_sides_labeled_at_the_user_message_whatever_the_tracking_mode(
    db, autotracking_on_ai_message,
):
    automaton = _automaton(autotracking_on_ai_message=autotracking_on_ai_message)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(BatchLiteSignalSource, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt).startswith(
        "[Turn 1]\n"
        "User: user says 0\n"
        "Assistant: assistant replies 0\n"
        "[Turn 2]\n"
        "User: user says 1\n"
        "Assistant: assistant replies 1\n"
        "[Turn 3]\n"
        "User: user says 2"
    )
    assert BATCH_LITE_TAG_INSTRUCTIONS in prompt


async def test_batch_lite_shortens_a_long_assistant_message_to_its_head_and_tail_and_keeps_a_short_one(db):
    head, middle, tail = "H" * ASSISTANT_EXCERPT_CHARS, "M" * 50, "T" * ASSISTANT_EXCERPT_CHARS
    at_threshold = "S" * (2 * ASSISTANT_EXCERPT_CHARS + 1)
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id, [head + middle + tail, at_threshold, "short"])

    prompt = await _prompt_sent_for(BatchLiteSignalSource, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt) == (
        "[Turn 1]\n"
        "User: user says 0\n"
        f"Assistant: {head}…{tail}\n"
        "[Turn 2]\n"
        "User: user says 1\n"
        f"Assistant: {at_threshold}\n"
        "[Turn 3]\n"
        "User: user says 2\n"
        "Assistant: short"
    )


async def test_batch_lite_puts_each_returned_row_on_its_own_user_message(db):
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)
    ai_service = _RecordingAiService(signals_csv="turn,mood\n1,10\n2,20\n3,30\n[eof]")
    source = _source(BatchLiteSignalSource, db, automaton, session_id, ai_service)

    await source.prepare_batch(turn_ids)

    moods = [(await source.get_turn_data(turn_id, ""))[0] for turn_id in turn_ids]
    assert moods == [{"mood": 10.0}, {"mood": 20.0}, {"mood": 30.0}]


@pytest.mark.parametrize("cls", [BatchSignalSource, BatchLiteSignalSource])
async def test_the_prompt_tells_the_model_to_rate_each_turn_without_what_comes_after_it(db, cls):
    automaton = _automaton(autotracking_on_ai_message=False)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(cls, db, automaton, session_id, turn_ids)

    assert TURN_HORIZON_INSTRUCTIONS in prompt


@pytest.mark.parametrize("cls", [BatchSignalSource, BatchLiteSignalSource])
@pytest.mark.parametrize(("autotracking_on_ai_message", "last_line"), [
    (True, "Assistant: assistant replies 2"),
    (False, "User: user says 2"),
])
async def test_the_last_turns_reply_is_shown_only_when_tracking_runs_after_the_ai_message(
    db, cls, autotracking_on_ai_message, last_line,
):
    automaton = _automaton(autotracking_on_ai_message=autotracking_on_ai_message)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    prompt = await _prompt_sent_for(cls, db, automaton, session_id, turn_ids)

    assert _transcript_in(prompt).splitlines()[-1] == last_line
