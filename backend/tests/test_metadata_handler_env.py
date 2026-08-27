"""Tests for MetadataHandler's [env]/[signals]/[audio] parsing, and for
how those values, together with a signal definition, get rendered into a
turn's prompt.

Two separate algorithms live side by side here, deliberately not unified:
parse_raw_signals/parse_raw_env (a single live turn, or
TurnByTurnSignalSource's one-call-per-turn replay — a forgiving
"key: value"-per-line format for env, plain JSON for signals, no turn
concept at all) and parse_batch_signals/parse_batch_env (BatchSignalSource,
covering several turns in one call — CSV rows / a JSON object keyed by
1-based turn number, checked for exact turn coverage). An earlier attempt
to share one turn-numbered format across both proved unstable for the
single-turn case (extra rows, wrong turn numbers, missing turn-number
prefix), so the two stay separate.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from tracking.metadata_handler import MetadataHandler, MetadataTurnMismatch
from tracking.turn_protocol import TurnProtocol

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_NAME = "proj"


def _handler() -> MetadataHandler:
    return MetadataHandler()


def test_parse_raw_env_reads_plain_key_value_lines():
    result = _handler().parse_raw_env("favorite_color: blue\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_strips_a_leading_dash_bullet():
    result = _handler().parse_raw_env("- favorite_color: blue\n- mood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_ignores_blank_lines_and_lines_without_a_colon():
    result = _handler().parse_raw_env("favorite_color: blue\n\njust some noise\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_of_empty_content_is_an_empty_dict():
    assert _handler().parse_raw_env("") == {}
    assert _handler().parse_raw_env(None) == {}


def test_parse_raw_env_handles_a_colon_inside_the_value():
    result = _handler().parse_raw_env("next_meeting: 14:30")
    assert result == {"next_meeting": "14:30"}


def test_parse_batch_env_reads_one_entry_per_turn_in_order():
    result = _handler().parse_batch_env(
        '{"1": {"favorite_color": "blue"}, "2": {}, "3": {"mood": "happy"}}', 3
    )
    assert result == [{"favorite_color": "blue"}, {}, {"mood": "happy"}]


def test_parse_batch_env_a_single_turn_call_reads_index_zero():
    result = _handler().parse_batch_env('{"1": {"next_meeting": "14:30"}}', 1)
    assert result[0] == {"next_meeting": "14:30"}


def test_parse_batch_env_of_empty_content_raises_turn_mismatch():
    """No entries at all never degrades to a silent empty result — even
    turn 1 alone must have an entry (an empty object is fine; a missing
    key is not), so this is treated the same as any other coverage gap."""
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env("", 1)
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env(None, 1)


def test_parse_batch_env_malformed_json_raises_turn_mismatch():
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env("not json at all", 1)


def test_parse_batch_env_a_non_object_top_level_raises_turn_mismatch():
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env("[1, 2, 3]", 1)


def test_parse_batch_env_extra_turns_beyond_what_was_expected_raises_turn_mismatch():
    """The exact bug this check exists for: the model treats the whole
    chat history as turns to fill in, instead of just the N it was told
    to cover."""
    raw = '{"1": {"a": "1"}, "2": {"a": "2"}, "3": {"a": "3"}}'
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env(raw, 1)


def test_parse_batch_env_a_missing_turn_raises_turn_mismatch():
    raw = '{"1": {}, "3": {"a": "b"}}'
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_env(raw, 3)


def test_parse_raw_signals_reads_a_flat_json_object():
    result = _handler().parse_raw_signals('{"mood": 50.2, "engagement": 70}')
    assert result == {"mood": 50.2, "engagement": 70}


def test_parse_raw_signals_of_empty_content_is_an_empty_dict():
    assert _handler().parse_raw_signals("") == {}
    assert _handler().parse_raw_signals(None) == {}


def test_parse_batch_signals_reads_one_row_per_turn_in_order():
    raw = "mood,engagement\n1,50.2,70\n2,52,68"
    result = _handler().parse_batch_signals(raw, 2)
    assert result == [{"mood": 50.2, "engagement": 70.0}, {"mood": 52.0, "engagement": 68.0}]


def test_parse_batch_signals_a_missing_row_raises_turn_mismatch():
    raw = "mood,engagement\n1,50.2,70\n3,55.5,71"
    with pytest.raises(MetadataTurnMismatch):
        _handler().parse_batch_signals(raw, 3)




class _RecordingProtocol(TurnProtocol):
    """A throwaway TurnProtocol subclass that captures whatever prompt
    __build_prompt assembled, instead of sending it to a real/fake AI
    service."""

    # Needs its own entries for every tag __build_prompt might look up,
    # since the base class default ({}) doesn't define any.
    prompt_preambles = {"env": "ENV:\n", "audio": "AUDIO:\n", "signals": "SIGNALS:\n", "text": ""}

    def __init__(self) -> None:
        super().__init__(ai_service=None, evaluate_signals_first=True)
        self.recorded_prompt: str | None = None

    def _generate_reply(self, prompt, chat_history, on_metadata):
        self.recorded_prompt = prompt

        async def _empty():
            return
            yield  # pragma: no cover - never reached, keeps this an async generator

        return _empty()

    def generate_reply_with_schema(self, base_prompt, tag_specs, chat_history, on_metadata):
        # Not exercised here — only present so this subclass stays instantiable.
        raise NotImplementedError


def test_generate_reply_renders_an_env_block_with_stored_values_only(db):
    """System/session facts live in the `system`/`session`
    evaluation-scope namespaces, never rendered into the prompt."""
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        revision=db.get_project_published_revision(PROJECT_NAME),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)
    env = PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))
    protocol = _RecordingProtocol()

    protocol.generate_reply("base prompt", "- Definition of signals:\n", env, [], lambda k, v: None)

    prompt = protocol.recorded_prompt
    assert "favorite_color: blue" in prompt
    assert "number_of_user_sessions" not in prompt
    assert "today:" not in prompt


def test_generate_reply_embeds_the_given_signal_definition_verbatim(db):
    """__build_prompt takes the already-rendered definition text directly
    — it has no opinion of its own on which signals it describes."""
    env = PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))
    protocol = _RecordingProtocol()

    protocol.generate_reply(
        "base prompt", '- Definition of signals:\n\t- Signal "mood":\nmood definition', env, [], lambda k, v: None
    )

    assert "mood definition" in protocol.recorded_prompt
