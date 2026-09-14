from __future__ import annotations

from dataclasses import dataclass, field

from automaton.build_error import AutomatonBuildError
from db import Db

from .archive.automaton_loader import AutomatonLoader


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    revision: int
    error: str | None
    warnings: list[str] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectHealth:
    project_id: str
    published: BuildOutcome | None
    draft: BuildOutcome


def broken_fields(health: ProjectHealth) -> dict:
    """{published, draft}: the build error of each, or None where it
    builds. One shape, read by the Manage projects row and by the design
    view's own toolbar — which of the two revisions is the broken one is
    the whole difference between "fix this file" and "publish what you
    already fixed"."""
    return {
        "published": health.published.error if health.published is not None else None,
        "draft": health.draft.error,
    }


class ProjectHealthChecker:
    """The one place that decides whether a project's stored index.yml
    still builds under today's AutomatonBuilder rules — published and
    draft revisions only, never an older one pinned by some session (see
    AutomatonLoader.load_at_revision's own force-close sweep for those)."""

    def __init__(self, db: Db, automaton_loader: AutomatonLoader) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
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
            return BuildOutcome(
                revision=revision, error=exc.detail or str(exc), problems=list(exc.problems),
                file=exc.file, line=exc.line,
            )
        except (ValueError, FileNotFoundError) as exc:
            return BuildOutcome(revision=revision, error=str(exc))
        return BuildOutcome(revision=revision, error=None, warnings=automaton.build_warnings)
