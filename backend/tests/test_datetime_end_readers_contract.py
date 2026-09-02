from __future__ import annotations

from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

ALLOWED_DATETIME_END_READERS = {
    "db/models.py",
    "db/sessions.py",
    "chat/session_manager.py",
    "chat/session_type_strategy.py",
    "chat/chat_service.py",
    "schemas.py",
    "tracking/session_export.py",
    "tracking/session_import.py",
    "metrics/metrics_framework/timeline.py",
    "metrics/metrics_framework/metrics/state_stability.py",
    "metrics/metrics_framework/benchmark_metrics/calculator.py",
    "metrics/metrics_framework/benchmark_metrics/test_benchmark_metrics.py",
    "testing/data.py",
}


@pytest.mark.contract
def test_datetime_end_is_only_read_by_the_allowlisted_files():
    offenders = sorted(
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if "datetime_end" in path.read_text(encoding="utf-8")
        and path.relative_to(SRC_ROOT).as_posix() not in ALLOWED_DATETIME_END_READERS
    )
    assert not offenders, (
        f"'datetime_end' referenced outside the allowlist in: {offenders}. "
        "Every openness decision must go through ChatSessionManager.is_open "
        "(see its docstring) — either use is_open/has_open_sessions_for_revision "
        "instead, or, for a genuine temporal read, add the file to "
        "ALLOWED_DATETIME_END_READERS in this test."
    )
