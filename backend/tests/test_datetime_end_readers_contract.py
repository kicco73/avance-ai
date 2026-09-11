from __future__ import annotations

import pytest

from conftest import SRC_ROOT, production_sources

ALLOWED_DATETIME_END_READERS = {
    "db/models.py",
    "db/sessions.py",
    "turn/turn_service.py",
    "turn/sessions/session_insights.py",
    "turn/sessions/session_manager.py",
    "turn/sessions/session_type_strategy.py",
    "schemas.py",
    "tracking/session_export.py",
    "tracking/session_import.py",
    "metrics/metrics_framework/timeline.py",
    "metrics/metrics_framework/metrics/state_stability.py",
    "metrics/metrics_framework/benchmark_metrics/calculator.py",
    "testing/data.py",
}


@pytest.mark.contract
def test_datetime_end_is_only_read_by_the_allowlisted_files():
    offenders = sorted(
        path.relative_to(SRC_ROOT).as_posix()
        for path in production_sources()
        if "datetime_end" in path.read_text(encoding="utf-8")
        and path.relative_to(SRC_ROOT).as_posix() not in ALLOWED_DATETIME_END_READERS
    )
    assert not offenders, (
        f"'datetime_end' referenced outside the allowlist in: {offenders}. "
        "Every openness decision must go through SessionManager.is_open "
        "(see its docstring) — either use is_open/has_open_sessions_for_revision "
        "instead, or, for a genuine temporal read, add the file to "
        "ALLOWED_DATETIME_END_READERS in this test."
    )
