from __future__ import annotations

import time
from datetime import datetime
from functools import wraps

from .models import DbUsage


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


def _timed(method):
    kind = 'write' if getattr(method, '_db_write', False) else 'read'

    @wraps(method)
    def wrapper(*args, **kwargs):
        started_at = datetime.utcnow()
        start = time.perf_counter()
        try:
            result = method(*args, **kwargs)
        except Exception:
            DbUsage.create(query_name=method.__name__, timestamp=started_at, duration=time.perf_counter() - start, outcome='failure', kind=kind)
            raise
        DbUsage.create(query_name=method.__name__, timestamp=started_at, duration=time.perf_counter() - start, outcome='success', kind=kind)
        return result
    return wrapper
