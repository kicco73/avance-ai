"""Every stored index.yml the format moved past, settled at boot rather
than at the first visit. The loader already repairs a revision that
fails to build (see automaton_loader.BasicAutomatonLoader._repaired),
in place, at that same revision — so this only loads what a running
product serves or an editor opens: each project's published revision
and its head. A revision the modernizer cannot settle is logged and left
as written, exactly as a visit would leave it."""
from __future__ import annotations

from automaton.build_error import AutomatonBuildError
from db import Db
from project.archive.automaton_loader import AutomatonLoader
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


def modernize_stored_revisions(db: Db) -> set[str]:
    loader = AutomatonLoader(db)
    touched: set[str] = set()
    for project_id in db.list_projects():
        revisions = {db.get_project_revision(project_id), db.get_project_published_revision(project_id)} - {None}
        for revision in sorted(revisions):
            touched |= _settled(loader, db, project_id, revision)
    return touched


def _settled(loader: AutomatonLoader, db: Db, project_id: str, revision: int) -> set[str]:
    before = db.get_archive(project_id, "index.yml", revision=revision)
    try:
        loader.load_at_revision(project_id, revision)
    except (AutomatonBuildError, FileNotFoundError) as refusal:
        logger.warning("Project '%s', revision %s: left as written — %s", project_id, revision, refusal)
        return set()
    after = db.get_archive(project_id, "index.yml", revision=revision)
    return {project_id for changed in [after != before] if changed}
