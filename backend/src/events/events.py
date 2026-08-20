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
    """A real (non-self-loop) transition — published by
    tracking.tracking_engine.TrackingEngine.notify_transition, right
    after the transition itself is persisted (see that method's own
    docstring for the exact two call sites)."""
    username: str
    project_name: str
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class EnvChanged:
    """One action-set env key's own write — published by
    tracking.tracking_engine.TrackingEngine.apply_action_env, once per
    key an action's own `env:` field actually wrote this turn."""
    username: str
    project_name: str
    key: str
    value: object


@dataclass(frozen=True, slots=True)
class AvailabilityChanged:
    """A project's own is_paused flag flipped (see project.
    project_service.ProjectService's own availability recomputation) —
    published only when the recomputed value actually differs from what
    was already saved (see that module's own docstring: the guard that
    makes a mutual dependency between two projects converge in a single
    pass, with no cycle detection needed)."""
    project_name: str
    available: bool
