from __future__ import annotations

from datetime import datetime, timedelta

from peewee import fn

from .models import AiTokenUsage

DEFAULT_HISTORY_DAYS = 30


class AiUsageMixin:

    def record_ai_token_usage(self, provider_label: str, input_tokens: int, output_tokens: int) -> None:
        AiTokenUsage.create(provider_label=provider_label, input_tokens=input_tokens, output_tokens=output_tokens)

    def get_ai_token_usage_snapshot(self, provider_labels: list[str], days: int = DEFAULT_HISTORY_DAYS) -> dict:
        """{'today': {label: tokens}, 'history': [{timestamp, values: {label:
        tokens}}, ...]} — one point per UTC calendar day over the trailing
        `days`, oldest first, grouped from the raw rows rather than kept as
        a running counter (see AiTokenUsage's own docstring). `today` is
        just history's own last day, pulled out separately so a caller
        that only wants the consumption bar's current value doesn't have
        to search the history list for it."""
        if not provider_labels:
            return {'today': {}, 'history': []}
        since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        # .cast('TEXT'): without it peewee infers `day`'s Python type from
        # AiTokenUsage.timestamp (a DateTimeField) and pads SQLite's own
        # 'YYYY-MM-DD' back out into a full datetime string, which then
        # doesn't match the 'YYYY-MM-DD' prefix the `today` check below
        # (and by_day's own keys) rely on.
        day = fn.date(AiTokenUsage.timestamp).cast('TEXT')
        rows = (
            AiTokenUsage
            .select(day.alias('day'), AiTokenUsage.provider_label, fn.SUM(AiTokenUsage.input_tokens + AiTokenUsage.output_tokens).alias('tokens'))
            .where(AiTokenUsage.provider_label.in_(provider_labels) & (AiTokenUsage.timestamp >= since))
            .group_by(day, AiTokenUsage.provider_label)
            .order_by(day.asc())
        )
        by_day: dict[str, dict[str, int]] = {}
        for row in rows:
            by_day.setdefault(row.day, {})[row.provider_label] = row.tokens
        history = [{'timestamp': f'{day_str}T00:00:00+00:00', 'values': values} for day_str, values in sorted(by_day.items())]
        today = history[-1]['values'] if history and history[-1]['timestamp'].startswith(datetime.utcnow().strftime('%Y-%m-%d')) else {}
        return {'today': today, 'history': history}
