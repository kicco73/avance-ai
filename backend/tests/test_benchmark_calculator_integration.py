"""Integration tests for BenchmarkCalculator's own DB-integration layer
(see metrics_framework/benchmark_metrics/calculator.py's _load_messages/
_load_signals) — specifically that it correctly sources expected_state/
expected_values from the Tracking row a message's evaluation produced (see
db.py's Tracking.message), not from the message itself.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from metrics.metrics_framework import BenchmarkCalculator


def _make_session(db, *, username="user", project_name="proj", start, start_state="a"):
    db.ensure_project(project_name)
    return db.create_chat_session(
        username=username,
        project_name=project_name,
        datetime_start=start,
        datetime_end=start,
        start_state=start_state,
        end_state=start_state,
    )


@pytest.mark.regression
def test_calculator_reads_expected_state_from_the_linked_signals_row(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 80}, session_id, message_id=message_id)
    db.set_signal_expected_state(signal_row_id, "a")

    observations = BenchmarkCalculator(db, "user", "proj").observations()

    assert len(observations) == 1
    assert observations[0].expected_state == "a"
    assert observations[0].message_id == message_id


@pytest.mark.regression
def test_unannotated_messages_alongside_an_annotated_one_produce_no_spurious_points(db):
    """Regression test: a `messages` DataFrame mixing a real annotated
    expected_state with several missing ones (pandas represents the
    missing ones as float NaN in that case, not None) used to make
    _points()' own `if row.expected_state:` check treat NaN as truthy —
    turning every *unannotated* message into its own spurious
    "expected_state == 'nan'" observation. Only the one real annotation
    must ever produce an observation here."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hi 1", session_id)
    message_id = db.save_message("user", "hi 2", session_id)
    db.save_message("user", "hi 3", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)
    db.set_signal_expected_state(signal_row_id, "a")

    observations = BenchmarkCalculator(db, "user", "proj").observations()

    assert len(observations) == 1
    assert observations[0].message_id == message_id
    assert observations[0].expected_state == "a"


@pytest.mark.regression
def test_calculator_reads_expected_values_from_the_linked_signals_row(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 80}, session_id, message_id=message_id)
    db.set_signal_expected_values(signal_row_id, {"foo": 80})

    observations = BenchmarkCalculator(db, "user", "proj").observations()

    assert len(observations) == 1
    assert observations[0].signal_agreements == {"foo": 100.0}


@pytest.mark.regression
def test_unlinked_signals_rows_never_produce_an_observation(db):
    """A manual action's transition (or any row auto-tracking never
    linked to a message) has nothing to annotate against — see
    Tracking.message's own docstring."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hi", session_id)
    signal_row_id = db.save_transition("", "init", "a", session_id, transition_log_level="INFO")
    db.set_signal_expected_state(signal_row_id, "a")  # would-be annotation, but unlinked

    observations = BenchmarkCalculator(db, "user", "proj").observations()

    assert observations == ()


@pytest.mark.contract
def test_calculator_is_scoped_to_one_session_when_given(db):
    session_a = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    message_a = db.save_message("user", "hi", session_a)
    message_b = db.save_message("user", "hi", session_b)
    row_a = db.save_signal_snapshot({"foo": 1}, session_a, message_id=message_a)
    row_b = db.save_signal_snapshot({"foo": 1}, session_b, message_id=message_b)
    db.set_signal_expected_state(row_a, "a")
    db.set_signal_expected_state(row_b, "a")

    observations = BenchmarkCalculator(db, "user", "proj", session_id=session_a).observations()

    assert len(observations) == 1
    assert observations[0].session_id == session_a


@pytest.mark.contract
def test_metrics_property_matches_calculate_all_order(db):
    calculator = BenchmarkCalculator(db, "user", "proj")
    results = calculator.calculate_all()
    assert [m.name for m in calculator.metrics] == [r.name for r in results]


@pytest.mark.regression
def test_unannotated_data_produces_zero_sample_metrics(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hi", session_id)

    results = {r.name: r for r in BenchmarkCalculator(db, "user", "proj").calculate_all()}

    assert results["state_accuracy"].sample_count == 0
    assert results["state_accuracy"].value == 0.0
