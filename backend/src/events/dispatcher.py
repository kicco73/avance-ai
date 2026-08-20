"""A minimal, in-process event registry — no framework, no persistence,
no ordering guarantee beyond "handlers run in the order they
subscribed". `publish(event)` calls every handler subscribed to
`type(event)`, synchronously; a handler that raises propagates straight
out of publish() rather than being caught defensively here.
"""
from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[Any], None]

_subscribers: dict[type, list[Handler]] = {}


def subscribe(event_type: type, handler: Handler) -> None:
    _subscribers.setdefault(event_type, []).append(handler)


def publish(event: Any) -> None:
    for handler in _subscribers.get(type(event), []):
        handler(event)


def _reset_for_tests() -> None:
    """Test-only — clears every subscription (see conftest.py's own
    per-test isolation for other module-level singletons). Never called
    from production code."""
    _subscribers.clear()
