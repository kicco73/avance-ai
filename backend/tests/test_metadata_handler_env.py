"""Tests for MemoryChannel/SignalsChannel's [memory]/[signals]/[audio]
decoding, and for how those values, together with a signal definition,
get rendered into a turn's prompt.

Two separate algorithms live side by side here, deliberately not unified:
SignalsChannel/MemoryChannel's single-turn decode (a single live turn, or
TurnByTurnSignalSource's one-call-per-turn replay — a forgiving
"key: value"-per-line format for memory, plain JSON for signals, no turn
concept at all) and SignalsBatchChannel/MemoryBatchChannel's decode
(BatchSignalSource, covering several turns in one call — CSV rows /
"<turn>:" headers followed by "key=value" lines, each ending in a final
"[eof]" line/row so truncation is provable rather than inferred from turn
count alone). Both batch decoders are strict, not lenient: any row/line
that doesn't fit the expected grammar raises immediately, on the spot —
there's no best-effort mode that salvages a partial result out of a
malformed or truncated response. An earlier attempt to share one
turn-numbered format across both proved unstable for the single-turn
case (extra rows, wrong turn numbers, missing turn-number prefix), so the
two stay separate.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import Env, PersistedEnv
from tracking.metadata_channels import (
	MemoryBatchChannel, MemoryChannel, MetadataTurnMismatch, SignalsBatchChannel, SignalsChannel, TextChannel,
)
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


def _memory_channel() -> MemoryChannel:
	return MemoryChannel(Env())


def _signals_channel() -> SignalsChannel:
	return SignalsChannel(None)


def test_parse_raw_env_reads_plain_key_value_lines():
	result = _memory_channel().decode("favorite_color: blue\nmood: happy")
	assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_strips_a_leading_dash_bullet():
	result = _memory_channel().decode("- favorite_color: blue\n- mood: happy")
	assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_ignores_blank_lines_and_lines_without_a_colon():
	result = _memory_channel().decode("favorite_color: blue\n\njust some noise\nmood: happy")
	assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_of_empty_content_is_an_empty_dict():
	assert _memory_channel().decode("") == {}
	assert _memory_channel().decode(None) == {}


def test_parse_raw_env_handles_a_colon_inside_the_value():
	result = _memory_channel().decode("next_meeting: 14:30")
	assert result == {"next_meeting": "14:30"}


def test_parse_batch_env_reads_one_entry_per_turn_in_order():
	raw = "1:\nfavorite_color=blue\n2:\n3:\nmood=happy\n[eof]"
	result = MemoryBatchChannel(expected_turns=3).decode(raw)
	assert result == [{"favorite_color": "blue"}, {}, {"mood": "happy"}]


def test_parse_batch_env_a_single_turn_call_reads_index_zero():
	result = MemoryBatchChannel(expected_turns=1).decode("1:\nnext_meeting=14:30\n[eof]")
	assert result[0] == {"next_meeting": "14:30"}


def test_parse_batch_env_of_empty_content_raises_turn_mismatch():
	"""No entries at all never degrades to a silent empty result — even
	turn 1 alone must have an entry (an empty object is fine; a missing
	header is not), so this is treated the same as any other coverage gap."""
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=1).decode("")
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=1).decode(None)


def test_parse_batch_env_garbage_content_raises_turn_mismatch_immediately():
	"""A line that's neither a turn header, a "key=value" pair, nor
	[eof] raises the instant it's reached — never logged-and-skipped so
	parsing can keep going and maybe salvage something usable from
	whatever comes after it."""
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=1).decode("not the expected format at all")


def test_parse_batch_env_a_malformed_line_mid_stream_raises_on_that_line():
	"""Fails immediately at the first bad line, not deferred to an
	end-of-parsing coverage check — the exception names the exact line,
	proving it was caught on the spot rather than inferred afterward
	from a turn count that didn't add up."""
	raw = "1:\nfavorite_color=blue\n2:\nthis line has no equals sign\n3:\na=b\n[eof]"
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		MemoryBatchChannel(expected_turns=3).decode(raw)
	assert "this line has no equals sign" in str(excinfo.value)


def test_parse_batch_env_a_truncated_tail_still_raises_no_partial_result_returned():
	"""A response cut off mid-generation (no closing [eof], turn 3 never
	started) still raises — the decode never returns a "best effort"
	list built from whatever arrived before the cut. The only thing
	salvaged is the diagnostic in the exception message; nothing is ever
	handed back to the caller as if it were a valid, complete result."""
	raw = "1:\nfavorite_color=blue\n2:\nmood=better"
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=3).decode(raw)


def test_parse_batch_env_extra_turns_beyond_what_was_expected_raises_turn_mismatch():
	"""The exact bug this check exists for: the model treats the whole
	chat history as turns to fill in, instead of just the N it was told
	to cover."""
	raw = "1:\na=1\n2:\na=2\n3:\na=3\n[eof]"
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=1).decode(raw)


def test_parse_batch_env_a_missing_turn_raises_turn_mismatch():
	raw = "1:\n3:\na=b\n[eof]"
	with pytest.raises(MetadataTurnMismatch):
		MemoryBatchChannel(expected_turns=3).decode(raw)


def test_parse_batch_env_every_turn_present_but_no_eof_marker_still_raises():
	"""Turn coverage alone is never trusted — a response cut off right
	before writing [eof] would otherwise look complete by coincidence."""
	raw = "1:\nfavorite_color=blue\n2:\n3:\nmood=happy"
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		MemoryBatchChannel(expected_turns=3).decode(raw)
	assert "no [eof] marker" in str(excinfo.value)


def test_parse_raw_signals_reads_a_flat_json_object():
	result = _signals_channel().decode('{"mood": 50.2, "engagement": 70}')
	assert result == {"mood": 50.2, "engagement": 70}


def test_parse_raw_signals_of_empty_content_is_an_empty_dict():
	assert _signals_channel().decode("") == {}
	assert _signals_channel().decode(None) == {}


def test_parse_batch_signals_reads_one_row_per_turn_in_order():
	raw = "mood,engagement\n1,50.2,70\n2,52,68\n[eof]"
	result = SignalsBatchChannel(None, expected_turns=2).decode(raw)
	assert result == [{"mood": 50.2, "engagement": 70.0}, {"mood": 52.0, "engagement": 68.0}]


def test_parse_batch_signals_a_missing_row_raises_turn_mismatch():
	raw = "mood,engagement\n1,50.2,70\n3,55.5,71\n[eof]"
	with pytest.raises(MetadataTurnMismatch):
		SignalsBatchChannel(None, expected_turns=3).decode(raw)


def test_parse_batch_signals_a_non_numeric_row_raises_immediately():
	"""Strict like MemoryBatchChannel: a row that doesn't parse is never
	logged-and-skipped so the rows around it can still be salvaged."""
	raw = "mood,engagement\n1,50.2,70\nnot-a-turn,1,2\n[eof]"
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		SignalsBatchChannel(None, expected_turns=2).decode(raw)
	assert "not-a-turn" in str(excinfo.value)


def test_parse_batch_signals_every_row_present_but_no_eof_row_still_raises():
	raw = "mood,engagement\n1,50.2,70\n2,52,68"
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		SignalsBatchChannel(None, expected_turns=2).decode(raw)
	assert "no [eof] marker" in str(excinfo.value)


def test_generate_reply_renders_an_env_block_with_stored_values_only(db):
	"""System/session facts live in the `system`/`session`
	evaluation-scope namespaces, never rendered into the prompt."""
	db.ensure_project(PROJECT_ID)
	db.publish_project(PROJECT_ID)
	session_id = db.create_chat_session(
		username=USERNAME, project_id=PROJECT_ID,
		revision=db.get_project_published_revision(PROJECT_ID),
		datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
		start_state="a", end_state="a",
	)
	db.set_env(session_id, {"favorite_color": "blue"})
	env = PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)
	channels = [SignalsChannel("- Definition of signals:\n"), TextChannel("base prompt"), MemoryChannel(env)]

	prompt = TurnProtocolUsingSchema(ai_service=None).build_final_prompt(channels)

	assert "favorite_color: blue" in prompt
	assert "number_of_user_sessions" not in prompt
	assert "today:" not in prompt


def test_generate_reply_embeds_the_given_signal_definition_verbatim(db):
	"""build_final_prompt takes the already-rendered definition text
	directly — it has no opinion of its own on which signals it
	describes."""
	env = PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID))
	channels = [
		SignalsChannel('- Definition of signals:\n\t- Signal "mood":\nmood definition'),
		TextChannel("base prompt"), MemoryChannel(env),
	]

	prompt = TurnProtocolUsingSchema(ai_service=None).build_final_prompt(channels)

	assert "mood definition" in prompt
