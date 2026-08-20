"""A minimal, in-process event registry — no framework, no persistence,
no ordering guarantee beyond "handlers run in the order they
subscribed": `publish(event)` just calls every handler subscribed to
`type(event)`, synchronously, one after another. A handler that raises
propagates straight out of publish() — callers that publish from inside
a turn's own critical path (see tracking.tracking_engine.TrackingEngine)
already run inside their own try/except-shaped error handling, so this
never adds a defensive layer of its own on top.

Module-level state (not a class needing DI): every publisher/subscriber
in the app shares the exact same one registry, the same way jobs/
job_queue.py's own ephemeral_job_queue is shared as a single instance
rather than each caller building its own.
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
