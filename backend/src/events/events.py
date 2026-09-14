"""The minimal set of cross-project events this app publishes (see
dispatcher.py's own subscribe/publish) — one dataclass per event type,
no base class or envelope: a handler subscribes to the exact type it
cares about (see dispatcher.subscribe), so there's nothing shared
between them worth factoring out.

What happens to one conversation is not here: it is addressed to a user
and a session, so it travels on the Bus as a message (see
system/bus.py). What is left here is about a project as a whole.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AvailabilityChanged:
    """A project's is_paused flag flipped — published only when the
    recomputed value actually differs from what was already saved,
    which is what makes a mutual dependency converge without cycle detection."""
    project_id: str
    available: bool


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
