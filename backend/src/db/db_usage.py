from __future__ import annotations

from datetime import datetime, timedelta

from peewee import fn

from .instrumentation import instrument_queries, usage_recorder
from .models import DbUsage

DEFAULT_HISTORY_HOURS = 24
OVERALL_KEY = 'overall'


@instrument_queries
class DbUsageMixin:

    def get_db_usage_snapshot(self, hours: int = DEFAULT_HISTORY_HOURS) -> dict:
        """{'request_history': [{timestamp, values: {query_name: call
        count}}, ...], 'history': [{timestamp, values: {query_name: mean
        call seconds, plus 'overall'/'read'/'write': the same mean over
        every successful call in that minute, all of them or split by
        DbUsage.kind}}, ...], 'error_history': [{timestamp, values:
        {query_name: failure count}}, ...]} — mirrors AiUsageMixin.
        get_ai_usage_snapshot (see db/ai_usage.py): `request_history` and
        `history` each have one point per UTC minute that saw a call
        (every outcome for the former, successful only for the latter),
        over the trailing `hours`, oldest first; `error_history` counts
        failed calls, one point per minute."""
        usage_recorder.flush()
        since = datetime.utcnow() - timedelta(hours=hours)
        minute = fn.strftime('%Y-%m-%dT%H:%M:00', DbUsage.timestamp).cast('TEXT')
        weighted_duration = fn.SUM(DbUsage.average_duration * DbUsage.count) / fn.SUM(DbUsage.count)
        request_rows = (
            DbUsage
            .select(minute.alias('minute'), DbUsage.query_name, fn.SUM(DbUsage.count).alias('count'))
            .where(DbUsage.timestamp >= since)
            .group_by(minute, DbUsage.query_name)
            .order_by(minute.asc())
        )
        requests_by_minute: dict[str, dict[str, int]] = {}
        for row in request_rows:
            requests_by_minute.setdefault(row.minute, {})[row.query_name] = row.count  # type: ignore[reportAttributeAccessIssue]
        request_history = [{'timestamp': f'{minute_str}+00:00', 'values': values} for minute_str, values in sorted(requests_by_minute.items())]

        rows = (
            DbUsage
            .select(minute.alias('minute'), DbUsage.query_name, weighted_duration.alias('duration'))
            .where((DbUsage.timestamp >= since) & (DbUsage.outcome == 'success'))
            .group_by(minute, DbUsage.query_name)
            .order_by(minute.asc())
        )
        by_minute: dict[str, dict[str, float]] = {}
        for row in rows:
            by_minute.setdefault(row.minute, {})[row.query_name] = row.duration  # type: ignore[reportAttributeAccessIssue]

        overall_rows = (
            DbUsage
            .select(minute.alias('minute'), weighted_duration.alias('duration'))
            .where((DbUsage.timestamp >= since) & (DbUsage.outcome == 'success'))
            .group_by(minute)
        )
        for row in overall_rows:
            by_minute.setdefault(row.minute, {})[OVERALL_KEY] = row.duration  # type: ignore[reportAttributeAccessIssue]

        kind_rows = (
            DbUsage
            .select(minute.alias('minute'), DbUsage.kind, weighted_duration.alias('duration'))
            .where((DbUsage.timestamp >= since) & (DbUsage.outcome == 'success'))
            .group_by(minute, DbUsage.kind)
        )
        for row in kind_rows:
            by_minute.setdefault(row.minute, {})[row.kind] = row.duration  # type: ignore[reportAttributeAccessIssue]

        history = [{'timestamp': f'{minute_str}+00:00', 'values': values} for minute_str, values in sorted(by_minute.items())]

        error_rows = (
            DbUsage
            .select(minute.alias('minute'), DbUsage.query_name, fn.SUM(DbUsage.count).alias('count'))
            .where((DbUsage.timestamp >= since) & (DbUsage.outcome == 'failure'))
            .group_by(minute, DbUsage.query_name)
            .order_by(minute.asc())
        )
        errors_by_minute: dict[str, dict[str, int]] = {}
        for row in error_rows:
            errors_by_minute.setdefault(row.minute, {})[row.query_name] = row.count  # type: ignore[reportAttributeAccessIssue]
        error_history = [{'timestamp': f'{minute_str}+00:00', 'values': values} for minute_str, values in sorted(errors_by_minute.items())]

        return {'request_history': request_history, 'history': history, 'error_history': error_history}
