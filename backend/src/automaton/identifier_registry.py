"""Every identifier a trigger/`env:` expression can reference, one dict
per namespace — shared by the identifiers API endpoint and
automaton_builder.py's build-time validation, so the two can't drift apart."""
from __future__ import annotations

from automaton.automaton import EnvKey, Signal
from metrics.metrics_framework import AnalyticsCalculator


class IdentifierRegistry:
    """The {namespace: {identifier: description}} registry every
    trigger/env: expression can reference: a project's own declared
    signals/env keys merged with the platform's fixed session/
    user/metric identifiers. `source.<name>.<method>` — like
    `automaton.<project>.*` — is deliberately absent from `build()` below:
    it's a dynamic, per-project namespace, so both its offline validation
    (AutomatonBuilder._validate_namespaced_expression, via
    TriggerExpressionAnalyzer.source_refs) and its registry entries for
    autocomplete (see project.inspector.ProjectInspector.
    get_identifier_registry, which merges one "source.<name>" entry per
    this project's own declared `sources:`) live outside this class."""

    SESSION: dict[str, str] = {
        "current_session_duration_in_minutes": "How long the current session has been running so far, in minutes.",
        "last_user_session_datetime": "The previous session's own start timestamp (UTC ISO-8601), or None for a user's very first session.",
        "number_of_user_sessions": "How many sessions this user has ever had in this project.",
        "state_duration_in_minutes": "How long the conversation has sat in its current state, in minutes.",
    }

    ACTUATOR: dict[str, str] = {
        "send_mail": "Sends an email — e.g. actuator.send_mail(user.email, 'Some **markdown** body').",
        "whatsapp": "Sends a WhatsApp message to a phone number already linked to a user account — e.g. actuator.whatsapp('34600000001', 'Some **markdown** body'). `phone_number` is E.164 digits, '+' optional. Returns True once sent, False for a number with no linked account or a failed delivery — nothing is sent in the False case. Once sent, it's also logged as an assistant message in the recipient's open session on this project, opening a new whatsapp session for them if they don't have one open.",
        "celebrate": "Plays a confetti animation in the frontend — e.g. actuator.celebrate().",
        "notify": "Shows a toast in the frontend — e.g. actuator.notify('Nice!', 'You reached **state B**.'). `body_md` is markdown.",
        "show": "Shows a dialog in the frontend with body_md as its content — e.g. actuator.show('**Full** details here.'). `body_md` is markdown.",
        "defer": "Runs another actuator call later — e.g. actuator.defer(lambda: actuator.send_mail(user.email, 'Reminder'), datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=env.reminder_days)). The first argument must be a `lambda:` with no arguments; `when` must be built from datetime.datetime(...) or datetime.datetime.now(...), optionally ± datetime.timedelta(...). The call survives a server restart: it is stored as text with a snapshot of `user`/`signal`/`env` as they were when deferred, under the user and project (never a session — `session.*` is not available in on-enter).",
        "prompt": "Runs an extra, synchronous, fully isolated model call — no conversation history, no attachments, no signal/env context, nothing persisted, just `prompt` in and its text back — e.g. actuator.notify('Note', actuator.prompt('Summarize the last exchange in one sentence.')).",
    }

    DATETIME: dict[str, str] = {
        "datetime": "Builds a specific date and time — e.g. datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc). Mainly used as actuator.defer's own `when` argument, which must be timezone-aware.",
        "timedelta": "A duration, for offsetting a datetime — e.g. datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1).",
    }

    DATETIME_TIMEZONE: dict[str, str] = {
        "utc": "The UTC timezone — pass as tzinfo to build a timezone-aware datetime, e.g. datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc).",
    }

    # Every User field (db/models.py) except id — see
    # db.users.UserMixin.get_user_facts, this namespace's own source.
    USER: dict[str, str] = {
        "email": "The user's email address (also their login identity).",
        "name": "The user's display name, as reported by their auth provider.",
        "picture_url": "The user's avatar/profile picture URL, as reported by their auth provider.",
        "provider": "Which auth provider verified this account (e.g. \"google\").",
        "provider_user_id": "The auth provider's own opaque, stable id for this account.",
        "created_at": "When this account was first registered (UTC ISO-8601).",
        "last_login": "This account's most recent login (UTC ISO-8601).",
        "active_project": "The name of this user's currently active project, or None if none is set.",
        "role": "This user's platform role: \"user\", \"supervisor\", or \"admin\".",
        "whatsapp_phone_number": "The WhatsApp number linked to this account (E.164 digits), or None if none is linked.",
    }

    @staticmethod
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

    # The two places an expression can live see two different views of
    # the registry below. A `trigger:`/`env:` expression is evaluated
    # *inside* a session and may do nothing but read, so `actuator` is
    # out. An `on-enter` line is where actuators are called — and an
    # actuator.defer'd call runs long after the session that fired it is
    # over, so `session` (and everything under it) is out there instead.
    # Exclusion is by prefix: naming a namespace drops its nested ones too.
    TRIGGER_SCOPE_EXCLUDES: tuple[str, ...] = ("actuator",)
    ACTUATOR_SCOPE_EXCLUDES: tuple[str, ...] = ("session",)

    @staticmethod
    def excluding(registry: dict[str, dict[str, str]], excluded: tuple[str, ...]) -> dict[str, dict[str, str]]:
        return {
            namespace: names for namespace, names in registry.items()
            if not any(namespace == ns or namespace.startswith(ns + ".") for ns in excluded)
        }

    @classmethod
    def for_triggers(cls, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return cls.excluding(registry, cls.TRIGGER_SCOPE_EXCLUDES)

    @classmethod
    def for_actuators(cls, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return cls.excluding(registry, cls.ACTUATOR_SCOPE_EXCLUDES)

    @classmethod
    def build(cls, signals: list[Signal], env_keys: list[EnvKey]) -> dict[str, dict[str, str]]:
        """`signals`/`env_keys`: a project's own declared signals and env
        keys, used both at build time (before validating any trigger/env:
        expression) and off an already-built Automaton's own attributes."""
        return {
            "signal": {signal.name: signal.ui_description for signal in signals},
            "env": {env_key.name: env_key.ui_description or "" for env_key in env_keys},
            "session": dict(cls.SESSION),
            "session.metric": dict(cls.SESSION_METRIC),
            "user": dict(cls.USER),
            "actuator": dict(cls.ACTUATOR),
            "metric": dict(cls.METRIC),
            "datetime": dict(cls.DATETIME),
            "datetime.timezone": dict(cls.DATETIME_TIMEZONE),
        }
