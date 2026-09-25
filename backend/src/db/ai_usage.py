from __future__ import annotations

from datetime import datetime, timedelta

from peewee import Expression, fn

from .instrumentation import instrument_queries, write
from .models import AiUsage

DEFAULT_HISTORY_HOURS = 24
USAGE_TOTALS_GROUPS = ("session_id", "username", "project_id", "test_run_id")
_USAGE_TOTALS_TOKENS = ("input_tokens", "output_tokens", "thoughts_tokens", "cache_read_tokens", "cache_creation_tokens")
ERROR_BUCKET_SECONDS = 60


@instrument_queries
class AiUsageMixin:

    @write
    def record_ai_usage(
        self, provider_label: str, input_tokens: int, output_tokens: int,
        cache_read_tokens: int = 0, cache_creation_tokens: int = 0, duration: float = 0.0,
        time_to_first_chunk: float | None = None, outcome: str = 'success', thoughts_tokens: int = 0,
        time_to_first_thought: float | None = None, account: dict | None = None,
    ) -> None:
        AiUsage.create(
            provider_label=provider_label, input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens, cache_creation_tokens=cache_creation_tokens, duration=duration,
            time_to_first_chunk=time_to_first_chunk, outcome=outcome, thoughts_tokens=thoughts_tokens,
            time_to_first_thought=time_to_first_thought, **(account or {}),
        )

    def get_ai_usage_totals(self, group_by: tuple[str, ...]) -> list[dict]:
        unknown = set(group_by) - set(USAGE_TOTALS_GROUPS)
        if unknown:
            raise ValueError(f"Cannot group AI usage by {sorted(unknown)}; allowed: {USAGE_TOTALS_GROUPS}.")
        keys = [getattr(AiUsage, name) for name in (*group_by, "kind", "provider_label")]
        sums = [fn.SUM(getattr(AiUsage, name)).alias(name) for name in _USAGE_TOTALS_TOKENS]
        query = AiUsage.select(*keys, fn.COUNT(AiUsage.id).alias("calls"), *sums).group_by(*keys).dicts()
        return [dict(row) for row in query]

    def get_ai_usage_by_day(
        self, days: int, project_id: str | None = None, username: str | None = None,
        session_id: int | None = None, kinds: tuple[str, ...] | None = None, session_type: str | None = None,
    ) -> list[dict]:
        since = (datetime.utcnow() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        day = fn.strftime('%Y-%m-%d', AiUsage.timestamp).coerce(False)
        query = (
            AiUsage
            .select(
                day.alias("day"), AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens).alias("input_tokens"),
                fn.SUM(AiUsage.output_tokens).alias("output_tokens"),
                fn.SUM(AiUsage.thoughts_tokens).alias("thoughts_tokens"),
            )
            .where(
                (AiUsage.timestamp >= since)
                & self._ai_usage_scope(project_id, username, session_id, kinds, session_type)
            )
            .group_by(day, AiUsage.provider_label)
            .order_by(day)
            .dicts()
        )
        return [dict(row) for row in query]

    def get_ai_usage_since(
        self, since: datetime, project_id: str | None = None, username: str | None = None,
        session_id: int | None = None, kinds: tuple[str, ...] | None = None, session_type: str | None = None,
    ) -> list[dict]:
        query = (
            AiUsage
            .select(
                AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens).alias("input_tokens"),
                fn.SUM(AiUsage.output_tokens).alias("output_tokens"),
                fn.SUM(AiUsage.thoughts_tokens).alias("thoughts_tokens"),
            )
            .where((AiUsage.timestamp >= since) & self._ai_usage_scope(project_id, username, session_id, kinds, session_type))
            .group_by(AiUsage.provider_label)
            .dicts()
        )
        return [dict(row) for row in query]

    def get_ai_usage_by_turn(
        self, days: int, project_id: str, kinds: tuple[str, ...], session_type: str | None,
    ) -> list[dict]:
        since = (datetime.utcnow() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        query = (
            AiUsage
            .select(
                AiUsage.turn_id, AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens).alias("input_tokens"),
                fn.SUM(AiUsage.output_tokens).alias("output_tokens"),
                fn.SUM(AiUsage.thoughts_tokens).alias("thoughts_tokens"),
            )
            .where(
                (AiUsage.timestamp >= since) & AiUsage.turn_id.is_null(False)
                & self._ai_usage_scope(project_id, None, None, kinds, session_type)
            )
            .group_by(AiUsage.turn_id, AiUsage.provider_label)
            .dicts()
        )
        return [dict(row) for row in query]

    def get_ai_usage_usernames(self, project_id: str, kinds: tuple[str, ...], session_type: str | None) -> list[str]:
        query = (
            AiUsage.select(AiUsage.username).distinct()
            .where(AiUsage.username.is_null(False) & self._ai_usage_scope(project_id, None, None, kinds, session_type))
            .order_by(AiUsage.username)
        )
        return [row.username for row in query]

    def get_ai_usage_session_ids(
        self, project_id: str, username: str, kinds: tuple[str, ...], session_type: str | None,
    ) -> list[int]:
        query = (
            AiUsage.select(AiUsage.session_id).distinct()
            .where(AiUsage.session_id.is_null(False) & self._ai_usage_scope(project_id, username, None, kinds, session_type))
            .order_by(AiUsage.session_id.desc())
        )
        return [row.session_id for row in query]

    @staticmethod
    def _ai_usage_scope(
        project_id: str | None, username: str | None, session_id: int | None,
        kinds: tuple[str, ...] | None, session_type: str | None,
    ) -> Expression:
        scope: Expression = AiUsage.kind.in_(kinds) if kinds is not None else (AiUsage.id == AiUsage.id)
        for column, value in (
            (AiUsage.project_id, project_id), (AiUsage.username, username),
            (AiUsage.session_id, session_id), (AiUsage.session_type, session_type),
        ):
            scope &= (column == value) if value is not None else True
        return scope

    def get_ai_usage_snapshot(self, provider_labels: list[str], hours: int = DEFAULT_HISTORY_HOURS) -> dict:
        """{'today': {label: tokens}, 'today_cache_read': {label:
        cache_read_tokens}, 'history': [{timestamp, values: {label: tokens},
        cache_read: {label: cache_read_tokens}, duration: {label: mean call
        seconds}, time_to_first_chunk: {label: mean seconds to the first
        byte, only over the calls that ever produced one}}, ...],
        'provider_changes': [{timestamp, provider_label}, ...],
        'error_history': [{timestamp, values: {outcome: count}}, ...],
        'cache_read_ratio':
        {label: cache_read_tokens / input_tokens over the trailing `hours`}} —
        `history` has one point per UTC minute that saw any usage, over the
        trailing `hours`, oldest first, grouped from the raw rows rather
        than kept as a running counter (see AiUsage's own docstring); only
        successful calls count towards it; a failed call's own duration
        (typically a fast rejection or a fully-waited-out stall) would
        otherwise skew the mean it's meant to show. `today`/
        `today_cache_read` are their own independent totals-since-midnight
        queries rather than derived from history's last point — a minute-
        granularity window shorter than the day so far would otherwise
        under-report them. `cache_read_ratio` is share of *input* (not
        input+output) served from cache over `hours` — the number Manage
        services shows next to each provider's own total. `provider_changes`
        is every call in the window whose provider differs from the call
        before it — the cascade's failovers and the admin's own manual
        selections, read back off the rows rather than kept as their own
        log. `error_history` counts every non-"success" outcome
        (AiService._outcome_for's categories), one point per
        ERROR_BUCKET_SECONDS-wide slot (also its own independent query,
        not `history` split by outcome, since a failed call is excluded
        from `history` altogether — see above)."""
        if not provider_labels:
            return {
                'today': {}, 'today_cache_read': {}, 'history': [], 'provider_changes': [],
                'error_history': [], 'cache_read_ratio': {},
            }
        since = datetime.utcnow() - timedelta(hours=hours)
        minute = fn.strftime('%Y-%m-%dT%H:%M:00', AiUsage.timestamp).cast('TEXT')
        rows = (
            AiUsage
            .select(
                minute.alias('minute'), AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens + AiUsage.output_tokens).alias('tokens'),
                fn.SUM(AiUsage.cache_read_tokens).alias('cache_read'),
                fn.AVG(AiUsage.duration).alias('duration'),
                fn.AVG(AiUsage.time_to_first_chunk).alias('first_chunk'),
            )
            .where(
                AiUsage.provider_label.in_(provider_labels) & (AiUsage.timestamp >= since)
                & (AiUsage.outcome == 'success')
            )
            .group_by(minute, AiUsage.provider_label)
            .order_by(minute.asc())
        )
        by_minute: dict[str, dict[str, int]] = {}
        cache_read_by_minute: dict[str, dict[str, int]] = {}
        duration_by_minute: dict[str, dict[str, float]] = {}
        first_chunk_by_minute: dict[str, dict[str, float]] = {}
        for row in rows:
            by_minute.setdefault(row.minute, {})[row.provider_label] = row.tokens  # type: ignore[reportAttributeAccessIssue]
            cache_read_by_minute.setdefault(row.minute, {})[row.provider_label] = row.cache_read  # type: ignore[reportAttributeAccessIssue]
            duration_by_minute.setdefault(row.minute, {})[row.provider_label] = row.duration  # type: ignore[reportAttributeAccessIssue]
            if row.first_chunk is not None:  # type: ignore[reportAttributeAccessIssue]
                first_chunk_by_minute.setdefault(row.minute, {})[row.provider_label] = row.first_chunk  # type: ignore[reportAttributeAccessIssue]
        history = [
            {
                'timestamp': f'{minute_str}+00:00', 'values': values,
                'cache_read': cache_read_by_minute.get(minute_str, {}),
                'duration': duration_by_minute.get(minute_str, {}),
                'time_to_first_chunk': first_chunk_by_minute.get(minute_str, {}),
            }
            for minute_str, values in sorted(by_minute.items())
        ]

        provider_changes: list[dict] = []
        previous_label: str | None = None
        for row in (
            AiUsage
            .select(AiUsage.provider_label, AiUsage.timestamp)
            .where(AiUsage.provider_label.in_(provider_labels) & (AiUsage.timestamp >= since))
            .order_by(AiUsage.timestamp.asc(), AiUsage.id.asc())
        ):
            if previous_label is not None and row.provider_label != previous_label:
                provider_changes.append({'timestamp': f'{row.timestamp.isoformat()}+00:00', 'provider_label': row.provider_label})
            previous_label = row.provider_label

        error_bucket = fn.strftime(
            '%Y-%m-%dT%H:%M:%S',
            (fn.strftime('%s', AiUsage.timestamp).cast('INTEGER') / ERROR_BUCKET_SECONDS) * ERROR_BUCKET_SECONDS,
            'unixepoch',
        )
        error_rows = (
            AiUsage
            .select(error_bucket.alias('bucket'), AiUsage.outcome, fn.COUNT(AiUsage.id).alias('count'))
            .where(
                AiUsage.provider_label.in_(provider_labels) & (AiUsage.timestamp >= since)
                & (AiUsage.outcome != 'success')
            )
            .group_by(error_bucket, AiUsage.outcome)
            .order_by(error_bucket.asc())
        )
        errors_by_bucket: dict[str, dict[str, int]] = {}
        for row in error_rows:
            errors_by_bucket.setdefault(row.bucket, {})[row.outcome] = row.count  # type: ignore[reportAttributeAccessIssue]
        error_history = [
            {'timestamp': f'{bucket_str}+00:00', 'values': values}
            for bucket_str, values in sorted(errors_by_bucket.items())
        ]

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_rows = (
            AiUsage
            .select(
                AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens + AiUsage.output_tokens).alias('tokens'),
                fn.SUM(AiUsage.cache_read_tokens).alias('cache_read'),
            )
            .where(AiUsage.provider_label.in_(provider_labels) & (AiUsage.timestamp >= today_start))
            .group_by(AiUsage.provider_label)
        )
        today = {row.provider_label: row.tokens for row in today_rows}  # type: ignore[reportAttributeAccessIssue]
        today_cache_read = {row.provider_label: row.cache_read for row in today_rows}  # type: ignore[reportAttributeAccessIssue]

        ratio_rows = (
            AiUsage
            .select(
                AiUsage.provider_label,
                fn.SUM(AiUsage.input_tokens).alias('input_tokens'),
                fn.SUM(AiUsage.cache_read_tokens).alias('cache_read'),
            )
            .where(AiUsage.provider_label.in_(provider_labels) & (AiUsage.timestamp >= since))
            .group_by(AiUsage.provider_label)
        )
        cache_read_ratio = {
            row.provider_label: (row.cache_read / row.input_tokens) if row.input_tokens else 0.0  # type: ignore[reportAttributeAccessIssue]
            for row in ratio_rows
        }
        return {
            'today': today, 'today_cache_read': today_cache_read, 'history': history,
            'provider_changes': provider_changes, 'error_history': error_history, 'cache_read_ratio': cache_read_ratio,
        }
