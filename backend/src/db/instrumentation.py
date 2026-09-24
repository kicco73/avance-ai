from __future__ import annotations

import threading
import time
from datetime import datetime
from functools import wraps
from statistics import fmean, median

from .models import DbUsage, database


def write(method):
    """Marks a db-layer mixin method as a write for instrument_queries'
    own benefit — never inferred from the method's body, since a method
    that merely calls .save()/.create() on some self-healing read path
    (see UserMixin.get_active_project_id) isn't a write in the sense
    this labels, and a heuristic reading the source would get that
    wrong. Undecorated methods default to 'read'."""
    method._db_write = True
    return method


def instrument_queries(cls: type) -> type:
    """Class decorator for a db-layer mixin: times every public method it
    defines and records the call to DbUsage. Skips names starting with
    '_' (the mixins' own private helpers, e.g. row-to-dict formatters) and
    anything that isn't a plain instance method."""
    for name, attr in list(vars(cls).items()):
        if name.startswith('_') or not callable(attr) or isinstance(attr, (staticmethod, classmethod)):
            continue
        setattr(cls, name, _timed(attr))
    return cls


class _MinuteOfCalls:

    def __init__(self, target: object, minute: datetime) -> None:
        self.target = target
        self.minute = minute
        self._durations: dict[tuple[str, str, str], list[float]] = {}

    def add(self, query_name: str, kind: str, outcome: str, duration: float) -> None:
        self._durations.setdefault((query_name, kind, outcome), []).append(duration)

    def rows(self) -> list[dict]:
        return [
            {
                'timestamp': self.minute, 'query_name': query_name, 'kind': kind, 'outcome': outcome,
                'count': len(durations), 'average_duration': fmean(durations),
                'median_duration': median(durations), 'max_duration': max(durations),
            }
            for (query_name, kind, outcome), durations in self._durations.items()
        ]


class _UsageRecorder:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: _MinuteOfCalls | None = None

    def record(self, query_name: str, kind: str, outcome: str, duration: float) -> None:
        target = database.obj
        with self._lock:
            minute = datetime.utcnow().replace(second=0, microsecond=0)
            if self._current is None or self._current.target is not target or self._current.minute != minute:
                if self._current is not None and self._current.target is target:
                    _save(self._current.rows())
                self._current = _MinuteOfCalls(target, minute)
            self._current.add(query_name, kind, outcome, duration)

    def flush(self) -> None:
        with self._lock:
            if self._current is not None and self._current.target is database.obj:
                _save(self._current.rows())


def _save(rows: list[dict]) -> None:
    if not rows:
        return
    DbUsage.insert_many(rows).on_conflict(
        conflict_target=[DbUsage.timestamp, DbUsage.query_name, DbUsage.kind, DbUsage.outcome],
        preserve=[DbUsage.count, DbUsage.average_duration, DbUsage.median_duration, DbUsage.max_duration],
    ).execute()


usage_recorder = _UsageRecorder()


def _timed(method):
    kind = 'write' if getattr(method, '_db_write', False) else 'read'

    @wraps(method)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = method(*args, **kwargs)
        except Exception:
            usage_recorder.record(method.__name__, kind, 'failure', time.perf_counter() - start)
            raise
        usage_recorder.record(method.__name__, kind, 'success', time.perf_counter() - start)
        return result
    return wrapper
