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
from tracking.channels import (
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


def test_memory_decode_reads_forgiving_key_value_lines_ignoring_bullets_blanks_noise_and_empty_content():
	assert _memory_channel().decode("favorite_color: blue\nmood: happy") == {"favorite_color": "blue", "mood": "happy"}
	assert _memory_channel().decode("- favorite_color: blue\n- mood: happy") == {"favorite_color": "blue", "mood": "happy"}
	assert _memory_channel().decode("favorite_color: blue\n\njust some noise\nmood: happy") == {"favorite_color": "blue", "mood": "happy"}
	assert _memory_channel().decode("next_meeting: 14:30") == {"next_meeting": "14:30"}
	assert _memory_channel().decode("") == {}
	assert _memory_channel().decode(None) == {}


def test_memory_batch_decode_reads_one_entry_per_turn_in_order_with_a_single_turn_at_index_zero():
	raw = "1:\nfavorite_color=blue\n2:\n3:\nmood=happy\n[eof]"
	assert MemoryBatchChannel(expected_turns=3).decode(raw) == [{"favorite_color": "blue"}, {}, {"mood": "happy"}]
	assert MemoryBatchChannel(expected_turns=1).decode("1:\nnext_meeting=14:30\n[eof]")[0] == {"next_meeting": "14:30"}


@pytest.mark.parametrize(("raw", "expected_turns", "mentions"), [
	("", 1, None),
	(None, 1, None),
	("not the expected format at all", 1, None),
	("1:\nfavorite_color=blue\n2:\nthis line has no equals sign\n3:\na=b\n[eof]", 3, "this line has no equals sign"),
	("1:\nfavorite_color=blue\n2:\nmood=better", 3, None),
	("1:\na=1\n2:\na=2\n3:\na=3\n[eof]", 1, None),
	("1:\n3:\na=b\n[eof]", 3, None),
	("1:\nfavorite_color=blue\n2:\n3:\nmood=happy", 3, "no [eof] marker"),
], ids=[
	"empty", "none", "garbage", "malformed-line-mid-stream", "truncated-tail", "extra-turns", "missing-turn", "no-eof",
])
def test_memory_batch_decode_raises_on_the_spot_for_anything_that_is_not_a_complete_well_formed_response(raw, expected_turns, mentions):
	"""Never logged-and-skipped so parsing can keep going and salvage a
	partial result; never trusting turn coverage alone (a response cut off
	right before [eof] would otherwise look complete by coincidence); and
	never accepting more turns than the N the model was told to cover —
	the exact bug this check exists for."""
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		MemoryBatchChannel(expected_turns=expected_turns).decode(raw)
	if mentions is not None:
		assert mentions in str(excinfo.value)


def test_signals_decode_reads_a_flat_json_object_or_an_empty_dict_for_empty_content():
	assert _signals_channel().decode('{"mood": 50.2, "engagement": 70}') == {"mood": 50.2, "engagement": 70}
	assert _signals_channel().decode("") == {}
	assert _signals_channel().decode(None) == {}


def test_signals_batch_decode_reads_one_row_per_turn_in_order():
	raw = "mood,engagement\n1,50.2,70\n2,52,68\n[eof]"
	assert SignalsBatchChannel(None, expected_turns=2).decode(raw) == [{"mood": 50.2, "engagement": 70.0}, {"mood": 52.0, "engagement": 68.0}]


@pytest.mark.parametrize(("raw", "expected_turns", "mentions"), [
	("mood,engagement\n1,50.2,70\n3,55.5,71\n[eof]", 3, None),
	("mood,engagement\n1,50.2,70\nnot-a-turn,1,2\n[eof]", 2, "not-a-turn"),
	("mood,engagement\n1,50.2,70\n2,52,68", 2, "no [eof] marker"),
], ids=["missing-row", "non-numeric-row", "no-eof"])
def test_signals_batch_decode_is_as_strict_as_the_memory_one(raw, expected_turns, mentions):
	with pytest.raises(MetadataTurnMismatch) as excinfo:
		SignalsBatchChannel(None, expected_turns=expected_turns).decode(raw)
	if mentions is not None:
		assert mentions in str(excinfo.value)


def test_the_final_prompt_renders_stored_env_values_only_and_the_given_signal_definition_verbatim(db):
	"""System/session facts live in the `system`/`session` evaluation-scope
	namespaces, never rendered into the prompt; build_final_prompt takes
	the already-rendered definition text directly — it has no opinion of
	its own on which signals it describes."""
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
	channels = [
		SignalsChannel('- Definition of signals:\n\t- Signal "mood":\nmood definition'),
		TextChannel("base prompt"), MemoryChannel(env),
	]

	prompt = TurnProtocolUsingSchema(ai_service=None).build_final_prompt(channels)

	assert "favorite_color: blue" in prompt
	assert "mood definition" in prompt
	assert "number_of_user_sessions" not in prompt
	assert "today:" not in prompt
