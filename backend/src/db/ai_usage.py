from __future__ import annotations

from datetime import datetime, timedelta

from peewee import fn

from .models import AiTokenUsage

DEFAULT_HISTORY_HOURS = 24


class AiUsageMixin:

    def record_ai_token_usage(
        self, provider_label: str, input_tokens: int, output_tokens: int,
        cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
    ) -> None:
        AiTokenUsage.create(
            provider_label=provider_label, input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens, cache_creation_tokens=cache_creation_tokens,
        )

    def get_ai_token_usage_snapshot(self, provider_labels: list[str], hours: int = DEFAULT_HISTORY_HOURS) -> dict:
        """{'today': {label: tokens}, 'today_cache_read': {label:
        cache_read_tokens}, 'history': [{timestamp, values: {label: tokens},
        cache_read: {label: cache_read_tokens}}, ...], 'cache_read_ratio':
        {label: cache_read_tokens / input_tokens over the trailing `hours`}} —
        `history` has one point per UTC minute that saw any usage, over the
        trailing `hours`, oldest first, grouped from the raw rows rather
        than kept as a running counter (see AiTokenUsage's own docstring).
        `today`/`today_cache_read` are their own independent totals-since-
        midnight queries rather than derived from history's last point — a
        minute-granularity window shorter than the day so far would
        otherwise under-report them. `cache_read_ratio` is share of *input*
        (not input+output) served from cache over `hours` — the number
        Manage services shows next to each provider's own total."""
        if not provider_labels:
            return {'today': {}, 'today_cache_read': {}, 'history': [], 'cache_read_ratio': {}}
        since = datetime.utcnow() - timedelta(hours=hours)
        # .cast('TEXT'): without it peewee infers `minute`'s Python type
        # from AiTokenUsage.timestamp (a DateTimeField) and tries to parse
        # SQLite's own truncated 'YYYY-MM-DDTHH:MM:00' string back into a
        # datetime, which then doesn't match the ISO string by_minute's
        # own keys (and ordering) rely on.
        minute = fn.strftime('%Y-%m-%dT%H:%M:00', AiTokenUsage.timestamp).cast('TEXT')
        rows = (
            AiTokenUsage
            .select(
                minute.alias('minute'), AiTokenUsage.provider_label,
                fn.SUM(AiTokenUsage.input_tokens + AiTokenUsage.output_tokens).alias('tokens'),
                fn.SUM(AiTokenUsage.cache_read_tokens).alias('cache_read'),
            )
            .where(AiTokenUsage.provider_label.in_(provider_labels) & (AiTokenUsage.timestamp >= since))
            .group_by(minute, AiTokenUsage.provider_label)
            .order_by(minute.asc())
        )
        by_minute: dict[str, dict[str, int]] = {}
        cache_read_by_minute: dict[str, dict[str, int]] = {}
        for row in rows:
            by_minute.setdefault(row.minute, {})[row.provider_label] = row.tokens
            cache_read_by_minute.setdefault(row.minute, {})[row.provider_label] = row.cache_read
        history = [
            {'timestamp': f'{minute_str}+00:00', 'values': values, 'cache_read': cache_read_by_minute.get(minute_str, {})}
            for minute_str, values in sorted(by_minute.items())
        ]

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_rows = (
            AiTokenUsage
            .select(
                AiTokenUsage.provider_label,
                fn.SUM(AiTokenUsage.input_tokens + AiTokenUsage.output_tokens).alias('tokens'),
                fn.SUM(AiTokenUsage.cache_read_tokens).alias('cache_read'),
            )
            .where(AiTokenUsage.provider_label.in_(provider_labels) & (AiTokenUsage.timestamp >= today_start))
            .group_by(AiTokenUsage.provider_label)
        )
        today = {row.provider_label: row.tokens for row in today_rows}
        today_cache_read = {row.provider_label: row.cache_read for row in today_rows}

        ratio_rows = (
            AiTokenUsage
            .select(
                AiTokenUsage.provider_label,
                fn.SUM(AiTokenUsage.input_tokens).alias('input_tokens'),
                fn.SUM(AiTokenUsage.cache_read_tokens).alias('cache_read'),
            )
            .where(AiTokenUsage.provider_label.in_(provider_labels) & (AiTokenUsage.timestamp >= since))
            .group_by(AiTokenUsage.provider_label)
        )
        cache_read_ratio = {
            row.provider_label: (row.cache_read / row.input_tokens) if row.input_tokens else 0.0
            for row in ratio_rows
        }
        return {'today': today, 'today_cache_read': today_cache_read, 'history': history, 'cache_read_ratio': cache_read_ratio}
