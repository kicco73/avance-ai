"""The "Vueling Refund" sample project (backend/samples/projects/Vueling
Refund) is the worked example PROJECT_SPECS.md points to for "the model
proposes, the script verifies" (source.<name>.select(...) != ''
existence checks) and for the new `value(*values, key=...)` method
(§5.2) — this exercises both against the sample's own real index.yml
and CSVs rather than a hand-built fixture, so a regression in either
would actually break the shipped sample, not just an isolated unit test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from project.archive.automaton_loader import AutomatonLoader
from session import Session
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from metrics.metric_service import MetricService
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract

PROJECT_ID = "vueling_refunds"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "samples" / "projects" / "Vueling Refund"

# A customer with more than one booking on file — exercises "the first
# matching row" for value(), same as the sample's own ai-definition warns
# the model about for select().
CUSTOMER_EMAIL = "julien.fernandez@hotmail.com"


def _content_type(path: str) -> str:
    return "text/yaml" if path.endswith(".yml") else "text/csv" if path.endswith(".csv") else "text/plain"


@pytest.fixture
def automaton(db):
    files = {}
    for path in SAMPLE_DIR.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = str(path.relative_to(SAMPLE_DIR))
        if relative.startswith("aspect/") or relative.endswith(".css"):
            continue  # irrelevant to this test, and .css isn't text/yaml|csv
        files[relative] = path.read_bytes()
    content_types = {name: _content_type(name) for name in files}
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, files, content_types)
    revision = db.get_project_revision(PROJECT_ID)
    return AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)


def _scope(db, automaton, state_key: str, email: str, env: Env | None = None) -> dict:
    db.get_or_create_user(None, None, email, None, None)
    project_service = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_service)
    builder = EvaluationScopeBuilder(env or Env(), metrics, SessionFacts(db, project_service), UserFacts(db), db)
    with Session().impersonate(email):
        return builder.build(automaton, state_key, {})


def test_the_sample_builds(automaton):
    assert automaton.project_id == PROJECT_ID
    assert set(automaton.states) >= {"intake", "locate_booking", "verify_entitlement"}


def test_go_to_locate_booking_env_produces_scalars_not_tables(db, automaton):
    action = next(a for a in automaton.states["intake"].actions if a.name == "go_to_locate_booking")
    scope = _scope(db, automaton, "intake", CUSTOMER_EMAIL)

    result = automaton.eval_action_env(action, scope)

    # value(...) reads one cell, straight off the sample's own tickets.csv
    # (the first row for this email) — no header, no comma-joined table.
    assert result["flight"] == "VY6008"
    assert result["flight_date"] == "2026-07-31 11:15"
    assert result["pnr"] == ""
    # customer_record stays a select() table (deliberately, for scripts) —
    # more than one row for this customer, so more than one line back.
    assert result["customer_record"].startswith("codice_volo,")
    assert result["customer_record"].count("\n") >= 2


def test_the_exit_trigger_is_a_real_existence_check_against_env_and_the_source(db, automaton):
    action = next(a for a in automaton.states["intake"].actions if a.name == "go_to_locate_booking")
    intake_scope = _scope(db, automaton, "intake", CUSTOMER_EMAIL)
    defaults = automaton.eval_action_env(action, intake_scope)

    persisted_env = Env(action_set=defaults)
    verify_action = next(
        a for a in automaton.states["locate_booking"].actions if a.name == "go_to_verify_entitlement"
    )

    # pnr is still "" straight after go_to_locate_booking fired — the
    # trigger must not fire yet, exactly as the model proposing nothing
    # should never itself cause a transition.
    locate_scope = _scope(db, automaton, "locate_booking", CUSTOMER_EMAIL, env=persisted_env)
    assert automaton.evaluate_triggers("locate_booking", locate_scope) != "go_to_verify_entitlement"

    # Once the model reports (or the customer gives) a pnr, the trigger's
    # own source.tickets_sold.select_rows_containing(...) half is what actually verifies
    # the flight — never just "the model said so."
    persisted_env.update_action_set({"pnr": "ABC123"})
    locate_scope = _scope(db, automaton, "locate_booking", CUSTOMER_EMAIL, env=persisted_env)
    assert automaton.evaluate_triggers("locate_booking", locate_scope) == "go_to_verify_entitlement"
    assert verify_action.trigger  # sanity: the action really is the one that fired
