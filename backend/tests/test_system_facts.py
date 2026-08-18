"""Tests for tracking.system_facts.SystemFacts — the `system` namespace
a trigger/`env:` expression resolves against (see tracking.
evaluation_scope.EvaluationScopeBuilder). Zero dependencies: every
method just reads the current instant fresh, never persisted.
"""
from __future__ import annotations

import pytest

from tracking.system_facts import SystemFacts

pytestmark = pytest.mark.contract


def test_today_is_an_iso_date():
    assert SystemFacts().today().count("-") == 2  # ISO date, YYYY-MM-DD


def test_time_is_hh_mm_ss():
    assert SystemFacts().time().count(":") == 2
