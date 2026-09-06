from __future__ import annotations

from dataclasses import dataclass, field

from automaton.build_error import AutomatonBuildError
from db import Db

from .archive.automaton_loader import AutomatonLoader


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    revision: int
    error: str | None
    # The builder's own non-fatal warnings (Automaton.build_warnings) —
    # only ever populated on a successful build (a failed one produced no
    # Automaton to read them off); never new warnings of this checker's
    # own, just surfacing what AutomatonBuilder already computes.
    warnings: list[str] = field(default_factory=list)
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectHealth:
    project_id: str
    published: BuildOutcome | None
    draft: BuildOutcome


class ProjectHealthChecker:
    """The one place that decides whether a project's stored index.yml
    still builds under today's AutomatonBuilder rules — published and
    draft revisions only, never an older one pinned by some session (see
    AutomatonLoader.load_at_revision's own force-close sweep for those)."""

    def __init__(self, db: Db, automaton_loader: AutomatonLoader) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        # project_id -> whatever check() last returned for it — read via
        # last_checked() *before* the next check() overwrites it, which is
        # how ProjectManager.recompute_availability tells a real
        # broken<->healthy transition apart from a repeated result.
        # current() (used by read-only callers like the runtime-status
        # view or ensure_project_not_broken) never touches this, so a
        # background poll can never eat the transition a real recompute
        # would otherwise have noticed.
        self._last: dict[str, ProjectHealth] = {}

    def check(self, project_id: str) -> ProjectHealth:
        health = self.current(project_id)
        self._last[project_id] = health
        return health

    def current(self, project_id: str) -> ProjectHealth:
        published_revision = self._db.get_project_published_revision(project_id)
        published = (
            self._build_outcome(project_id, published_revision) if published_revision is not None else None
        )
        draft = self._build_outcome(project_id, self._db.get_project_revision(project_id))
        return ProjectHealth(project_id=project_id, published=published, draft=draft)

    def last_checked(self, project_id: str) -> ProjectHealth | None:
        return self._last.get(project_id)

    def _build_outcome(self, project_id: str, revision: int) -> BuildOutcome:
        try:
            automaton = self._automaton_loader.load_at_revision(project_id, revision)
        except AutomatonBuildError as exc:
            return BuildOutcome(revision=revision, error=exc.detail or str(exc), file=exc.file, line=exc.line)
        except (ValueError, FileNotFoundError) as exc:
            return BuildOutcome(revision=revision, error=str(exc))
        return BuildOutcome(revision=revision, error=None, warnings=automaton.build_warnings)
