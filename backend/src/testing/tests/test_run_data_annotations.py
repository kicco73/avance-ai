from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from testing.data import TestDataBuilder
from tracking.tracking_engine import TestObservationSink
from turn.turn_transaction import RowHandle

pytestmark = pytest.mark.contract


def _automaton() -> Automaton:
    init_action = Action(name="init", ui_label="init", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
            "a": State(input_processor="ai", key="a", ui_label="a", final=False, actions=[
                Action(name="stay", ui_label="stay", ui_button="", target="a", trigger="signal.empathy > 100"),
            ]),
        },
        general_prompt="general",
        signals=[Signal(name="empathy", ui_label="Empathy", definition="whatever")],
        general_attachments={},
        autotracking_on_ai_message=True,
    )


def test_a_replay_evaluating_on_the_ai_reply_is_scored_against_the_annotation_on_the_user_message(app_db):
    app_db.ensure_project("p")
    session_id = app_db.create_chat_session(
        "tester", "p", 1, datetime_start=datetime(2026, 9, 26, 4, 20), start_state="a", type="imported",
    )
    user_message_id = app_db.save_message("user", "hola", session_id, timestamp=datetime(2026, 9, 26, 4, 20, 35))
    ai_message_id = app_db.save_message("assistant", "hola", session_id, timestamp=datetime(2026, 9, 26, 4, 20, 40))
    app_db.import_tracking_row(
        session_id, old_state=None, action=None, new_state=None, values=None, expected_state="a",
        expected_values={"empathy": 60}, comment=None, message_id=user_message_id,
        timestamp=datetime(2026, 9, 26, 4, 20, 35),
    )
    run = app_db.create_test("tester", "p", session_id, "batch_lite", 0, None, {})
    TestObservationSink(run["id"]).save_transition(
        "a", "stay", "a", session_id, "INFO", signal_values={"empathy": 50}, message_id=RowHandle(ai_message_id),
    )

    data = TestDataBuilder.build(app_db, app_db.get_test(run["id"]), _automaton())
    results = {result.name: result for result in BenchmarkCalculator.from_data(data).calculate_all()}

    assert (results["signal_accuracy"].value, results["signal_accuracy"].sample_count) == (90.0, 1)
    assert (results["state_accuracy"].value, results["state_accuracy"].sample_count) == (100.0, 1)
