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
    user/metric identifiers. `source.<name>.<method>` and
    `media.<doc_id>.url` are deliberately absent from `build()` below:
    each is a dynamic, per-project namespace, so both its offline
    validation (AutomatonValidator.validate_namespaced_expression, via
    TriggerExpressionAnalyzer.source_refs/media_refs) and its registry
    entries for autocomplete (see project.inspector.ProjectInspector.
    get_identifier_registry, which merges one "source.<name>" entry per
    this project's own declared `sources:` and one "media.<doc_id>"
    entry per file uploaded under this project's own `media/` folder)
    live outside this class. `media` itself is on-exit only (see
    ON_EXIT_SCOPE_EXCLUDES/TRIGGER_SCOPE_EXCLUDES/TASK_SCOPE_EXCLUDES
    below) — there is no reason to hand out a download link from a
    trigger or a task script."""

    SESSION: dict[str, str] = {
        "current_session_duration_in_minutes": "How long the current session has been running so far, in minutes.",
        "last_user_session_datetime": "The previous session's own start timestamp (UTC ISO-8601), or None for a user's very first session.",
        "number_of_user_sessions": "How many sessions this user has ever had in this project.",
        "state_duration_in_minutes": "How long the conversation has sat in its current state, in minutes.",
    }

    TASK: dict[str, str] = {
        "send_mail": "Sends an email — e.g. task.send_mail(user.email, 'Some **markdown** body').",
        "whatsapp": "Sends a WhatsApp message to a phone number already linked to a user account — e.g. task.whatsapp('34600000001', 'Some **markdown** body'). `phone_number` is E.164 digits, '+' optional. Returns True once sent, False for a number with no linked account or a failed delivery — nothing is sent in the False case. Once sent, it's also logged as an assistant message in the recipient's open session on this project, opening a new whatsapp session for them if they don't have one open.",
        "defer": "Runs another task call later — e.g. task.defer(lambda: task.send_mail(user.email, 'Reminder'), datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=env.reminder_days)). The first argument must be a `lambda:` with no arguments; `when` must be built from datetime.datetime(...) or datetime.datetime.now(...), optionally ± datetime.timedelta(...). The call survives a server restart: it is stored as text with a snapshot of `user`/`signal`/`env` as they were when deferred, under the user and project (never a session — `session.*` is not available in task).",
        "websearch": "Searches the web and returns what it found as CSV — e.g. task.websearch('well-reviewed dentists in Barcelona'). The pages a search engine returns are read, a model decides which columns describe them and fills them in, and the CSV text comes back (header row first) for a later statement in the same task to use. The same CSV is also kept for the user now talking, where a source declared `url: websearch:user` reads it — the pages themselves are the same ones the Source card's own AI Web Import crawls.",
        "prompt": "Runs an extra, synchronous, fully isolated model call — no conversation history, no attachments, no signal/env context, nothing persisted, just `prompt` in and its text back — e.g. task.send_mail(user.email, task.prompt('Summarize the last exchange in one sentence.')).",
    }

    CHAT: dict[str, str] = {
        "celebrate": "Plays a confetti animation in the frontend — e.g. chat.celebrate(). Only available in an action's own on-exit script.",
        "notify": "Shows a toast in the frontend — e.g. chat.notify('Nice!', 'You reached **state B**.'). `body_md` is markdown. Only available in an action's own on-exit script.",
        "show": "Shows a dialog in the frontend with body_md as its content — e.g. chat.show('**Full** details here.'). `body_md` is markdown. Only available in an action's own on-exit script.",
        "show_media": "Shows one of this project's own media/ files in the frontend — e.g. chat.show_media(media.report.url()). An image, PDF, or Markdown file opens in a dialog; an audio file plays in a looping background player instead. `url` is a media file's own download url, e.g. media.<doc_id>.url(). Only available in an action's own on-exit script, and only takes effect in webchat.",
        "switch_to_human": "Hands the session to a person — e.g. chat.switch_to_human(user.email). `user_id` is that person's username/email; they get pushed a notification with a link to take over this session's next turns as the human, in place of the AI. Only available in an action's own on-exit script.",
        "switch_to_ai": "Hands a session back to the AI after switch_to_human — e.g. chat.switch_to_ai(). Only available in an action's own on-exit script.",
        "chart": "Shows a bar chart in the frontend — e.g. chat.chart('Scores', {'line': 'Empathy', 'value': 7.5}, {'line': 'Focus', 'value': 4}, max_scale=10). One {'line': ..., 'value': ...} dict per bar, each its own argument. `max_scale` is the value a full bar stands for: without it the chart scales to its own values, so the largest bar is always full — give it when the values are scores out of a known maximum. Only available in an action's own on-exit script, and only reaches a connection showing this conversation.",
        "progress": "Shows a progress bar in the chat, under the current turn's own message — e.g. chat.progress('Uploading', 42). `percentage` is 0-100; the bar stays until a later call reaches 100 or higher. Only available in an action's own on-exit script, and only reaches a connection showing this conversation.",
        "write": "Writes body_md as the reply of the input-processor: system state this action leads to — e.g. chat.write('Step %d of %d' % (env.step + 1, len(env.questions))). Several calls join as paragraphs; the text is saved in the transcript like any reply. Only available in the on-exit script of an action whose target state declares input-processor: system.",
    }

    DRIVE: dict[str, str] = {
        "read": "Returns exactly what this project last wrote for the person now talking, at that path, text or bytes as it was written — e.g. drive.read('reports/last.md'). \"\" when nothing has been written there: a drive starts empty, and reading before writing is normal, not a failure. Only available in a task script.",
        "write": "Writes text or bytes, verbatim, into this project's own space for the person now talking, creating the file or replacing it — e.g. drive.write('reports/last.md', report). Nothing else is ever accepted. The '/' are part of the name, not folders to create. Returns the path written. Only available in a task script.",
        "list": "The paths written for the person now talking that start with `prefix`, in order — e.g. drive.list('reports/'); no prefix means everything. Only available in a task script.",
        "delete": "Removes one of this person's files — e.g. drive.delete('reports/last.md'). True if there was something to remove. Only available in a task script.",
    }

    ATTACHMENT: dict[str, str] = {
        "read": "Returns one of this project's own archive files' whole text content — e.g. attachment.read('policy.txt'). Reachable from an action's on-exit and task scripts, never from a trigger. `name` must be a string literal (exact archive path, or a unique basename under behaviour/); the file must exist, be text, and be under the size limit — all checked when the project is built, not when this runs.",
    }

    DATETIME: dict[str, str] = {
        "datetime": "Builds a specific date and time — e.g. datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc). Mainly used as task.defer's own `when` argument, which must be timezone-aware.",
        "timedelta": "A duration, for offsetting a datetime — e.g. datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1).",
    }

    DATETIME_TIMEZONE: dict[str, str] = {
        "utc": "The UTC timezone — pass as tzinfo to build a timezone-aware datetime, e.g. datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc).",
    }
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
    SESSION_METRIC: dict[str, str] = _metric_descriptions(has_scope="one_session")
    METRIC: dict[str, str] = _metric_descriptions(has_scope="all_sessions_per_user", excludes_scope="one_session")
    TRIGGER_SCOPE_EXCLUDES: tuple[str, ...] = ("task", "attachment", "chat", "drive", "media")
    TASK_SCOPE_EXCLUDES: tuple[str, ...] = ("session", "chat", "media")
    ON_EXIT_SCOPE_EXCLUDES: tuple[str, ...] = ("task", "drive")

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
    def for_task(cls, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return cls.excluding(registry, cls.TASK_SCOPE_EXCLUDES)

    @classmethod
    def for_on_exit(cls, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return cls.excluding(registry, cls.ON_EXIT_SCOPE_EXCLUDES)

    @classmethod
    def build(cls, signals: list[Signal], env_keys: list[EnvKey]) -> dict[str, dict[str, str]]:
        """`signals`/`env_keys`: a project's own declared signals and env
        keys, used both at build time (before validating any trigger/env:
        expression) and off an already-built Automaton's own attributes."""
        return {
            "signal": {signal.name: signal.ui_description for signal in signals},
            "env": {env_key.name: env_key.ai_definition or "" for env_key in env_keys},
            "session": dict(cls.SESSION),
            "session.metric": dict(cls.SESSION_METRIC),
            "user": dict(cls.USER),
            "task": dict(cls.TASK),
            "chat": dict(cls.CHAT),
            "attachment": dict(cls.ATTACHMENT),
            "drive": dict(cls.DRIVE),
            "metric": dict(cls.METRIC),
            "datetime": dict(cls.DATETIME),
            "datetime.timezone": dict(cls.DATETIME_TIMEZONE),
        }
