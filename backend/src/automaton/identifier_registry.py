"""Every identifier a trigger/`env:` expression can reference, one dict
per namespace — shared by the identifiers API endpoint and
automaton_builder.py's build-time validation, so the two can't drift apart."""
from __future__ import annotations

from automaton.automaton import EnvKey, Signal
from metrics.metrics_framework import AnalyticsCalculator

SYSTEM: dict[str, str] = {
    "today": "Today's date (UTC), as YYYY-MM-DD.",
    "time": "The current time of day (UTC), as HH:MM:SS.",
}

SESSION: dict[str, str] = {
    "current_session_duration_in_minutes": "How long the current session has been running so far, in minutes.",
    "last_user_session_datetime": "The previous session's own start timestamp (UTC ISO-8601), or None for a user's very first session.",
    "number_of_user_sessions": "How many sessions this user has ever had in this project.",
    "state_duration_in_minutes": "How long the conversation has sat in its current state, in minutes.",
}


def _metric_descriptions(*, has_scope: str, excludes_scope: str | None = None) -> dict[str, str]:
    return {
        metric.name: metric.ui_description
        for metric in AnalyticsCalculator.default_metrics()
        if has_scope in metric.scope and (excludes_scope is None or excludes_scope not in metric.scope)
    }


# Engagement/State Stability/Signal Stability — every default metric
# meaningful over just the current session's own window (see
# tracking.session_facts.SessionFacts.metric).
SESSION_METRIC: dict[str, str] = _metric_descriptions(has_scope="one_session")

# Retention/Activity Consistency — cross-session metrics. Excluding
# "one_session" matters: a metric's default scope is *every* MetricScope,
# so SESSION_METRIC's own metrics would otherwise match here too.
METRIC: dict[str, str] = _metric_descriptions(has_scope="all_sessions_per_user", excludes_scope="one_session")


def build_registry(signals: list[Signal], env_keys: list[EnvKey]) -> dict[str, dict[str, str]]:
    """`signals`/`env_keys`: a project's own declared signals and env
    keys, used both at build time (before validating any trigger/env:
    expression) and off an already-built Automaton's own attributes."""
    return {
        "signal": {signal.name: signal.ui_description for signal in signals},
        "env": {env_key.name: env_key.ui_description or "" for env_key in env_keys},
        "system": dict(SYSTEM),
        "session": dict(SESSION),
        "session.metric": dict(SESSION_METRIC),
        "metric": dict(METRIC),
    }
