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


@dataclass(frozen=True, slots=True)
class ProjectPublishedHealthChanged:
    """The published revision's own build outcome flipped broken<->healthy
    — never fired for the draft, and never repeated while it stays the
    same (see ProjectHealthChecker.check/ProjectManager.recompute_availability).
    Kept separate from AvailabilityChanged: that one already covers manual
    pause and automaton.* dependencies too, and this one exists only to
    drive the admin-facing broken-project notification."""
    project_id: str
    revision: int
    error: str | None
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectRevisionBuildFailed:
    """A stored revision just failed to build (see AutomatonLoader.
    load_at_revision) — published only when `revision` is the project's
    own current published or draft revision, never an older one pinned
    by some session alone (see AutomatonLoader._handle_broken_revision).
    AutomatonLoader has no reference to ProjectManager (and must never
    gain one — project/ already depends downward on it) so this is how a
    lazy build failure, discovered outside any publish/save flow,
    reaches ProjectManager.recompute_availability."""
    project_id: str
    revision: int
