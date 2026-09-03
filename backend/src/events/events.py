"""The minimal set of cross-project events this app publishes (see
dispatcher.py's own subscribe/publish) — one dataclass per event type,
no base class or envelope: a handler subscribes to the exact type it
cares about (see dispatcher.subscribe), so there's nothing shared
between them worth factoring out.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StateChanged:
    """A real (non-self-loop) transition — published right after the
    transition itself is persisted."""
    username: str
    project_id: str
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class EnvChanged:
    """One action-set env key's own write — published by
    tracking.tracking_engine.TrackingEngine.apply_action_env, once per
    key an action's own `env:` field actually wrote this turn."""
    username: str
    project_id: str
    key: str
    value: object


@dataclass(frozen=True, slots=True)
class AvailabilityChanged:
    """A project's is_paused flag flipped — published only when the
    recomputed value actually differs from what was already saved,
    which is what makes a mutual dependency converge without cycle detection."""
    project_id: str
    available: bool
