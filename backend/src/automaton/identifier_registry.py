"""The project-wide registry of every identifier a trigger/`env:`
expression can reference, one dict per namespace — signal, env, system,
session, session.metric, metric — each mapping an identifier to its own
human-readable description (never whether it's a variable or a
zero-arg proxy: the namespace alone already determines that — signal and
env are variables, every other namespace is proxies).

Not itself the source of truth for *whether* an identifier is valid (see
automaton.automaton.RESERVED_NAMESPACES/trigger_namespace_refs) — this
module exists so GET /api/chat/identifiers (see project_service.py's
get_active_identifier_registry, and controller.py) and automaton_builder.
py's own build-time validation (_validate_namespaced_expression) share
one identifier list, never two that could silently drift apart.

`system`/`session`/`session.metric`/`metric` are the same for every
project — hand-kept in sync with the classes that actually implement them
(tracking.system_facts.SystemFacts, tracking.session_facts.SessionFacts,
metrics.metric_namespace.SessionMetricNamespace/UserMetricNamespace),
same reason automaton/ has never imported tracking/ (see SYSTEM_ATTRS'
old equivalent, automaton_builder.py's previous own copy of this same
list, now replaced by this module). `signal`/`env` are read straight off
a project's own Signal/EnvKey declarations (`signals:`/`env:`, see
automaton_builder.py's build()) — no second source of truth for either.
`env` used to be collected dynamically off every action's own `env:`
field instead (whichever key *any* action happened to write to,
project-wide) — replaced by a real declared `env:` section (parallel to
`signals:`) so an action's own env: write can itself be validated against
something (see automaton_builder.py's _actions_sanity_check), not just
its reads.
"""
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

# Retention/Activity Consistency — every default metric meaningful only
# over the user's whole cross-session history (see metrics.metric_service.
# MetricService.for_turn). Excluding "one_session" (not just checking
# "all_sessions_per_user" in scope) matters: a metric's own default scope
# is *every* MetricScope (see BaseMetric.scope), so Engagement/
# StateStability/SignalStability would otherwise match here too — those
# belong to SESSION_METRIC above instead, never both.
METRIC: dict[str, str] = _metric_descriptions(has_scope="all_sessions_per_user", excludes_scope="one_session")


def build_registry(signals: list[Signal], env_keys: list[EnvKey]) -> dict[str, dict[str, str]]:
    """`signals`/`env_keys`: a project's own declared signals and env keys
    (see automaton_builder.py's build(), which calls this once both are
    fully assembled but before validating any trigger/env: expression
    against it — and project_service.py's get_active_identifier_registry,
    which calls this the same way off an already-built Automaton's own
    .signals/.env_keys)."""
    return {
        "signal": {signal.name: signal.ui_description for signal in signals},
        "env": {env_key.name: env_key.ui_description or "" for env_key in env_keys},
        "system": dict(SYSTEM),
        "session": dict(SESSION),
        "session.metric": dict(SESSION_METRIC),
        "metric": dict(METRIC),
    }
