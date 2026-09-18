"""One-time, whole-database fix-up for Archive rows still stored under
`aspect/<name>` now that images/audio/pdf/markdown canonicalize under
`media/<name>` instead (see automaton.file_types). ProjectEditor.
migrate_legacy_media_assets does the same rename through the ordinary
edit path, but only for one project's current draft — a published
revision, or an older one some session is still pinned to, never goes
through it. This covers every stored revision of every project in one
pass, rewriting rows directly rather than through that edit path: a
system migration correcting existing data is not the same kind of write
as a person editing a file, and has no undo/redo history to keep.
"""
from __future__ import annotations

from pathlib import Path

from automaton.file_types import ASPECT_DIR, MEDIA_DIR, ProjectFileTypes
from db import Db
from db.models import Archive
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

_ASPECT_PREFIX = f"{ASPECT_DIR}/"
_MEDIA_PREFIX = f"{MEDIA_DIR}/"


def migrate_aspect_archives(db: Db) -> set[str]:
    """Renames every Archive row under `aspect/` whose extension now
    belongs under MEDIA_DIR to `media/<basename>`, in place, at whatever
    revision it was found. Returns the project ids touched, empty when
    nothing needed it — a cheap existence check first means an ordinary
    boot, on a database with nothing left under `aspect/`, pays for one
    extra query and nothing else."""
    if not Archive.select().where(Archive.archive_name.startswith(_ASPECT_PREFIX)).exists():
        return set()
    renames = _renames()
    if not renames:
        return set()
    db.backup_now(f"renaming {len(renames)} archive row(s) from {_ASPECT_PREFIX} to {_MEDIA_PREFIX}")
    touched: set[str] = set()
    for row, new_name in renames:
        Archive.update(archive_name=new_name).where(Archive.id == row.id).execute()
        touched.add(row.project_id)
    logger.warning(
        "Media migration: renamed %s archive row(s) across %s project(s) from %s to %s.",
        len(renames), len(touched), _ASPECT_PREFIX, _MEDIA_PREFIX,
    )
    return touched


def _renames() -> list[tuple[Archive, str]]:
    existing_names = {
        (row.project_id, row.revision, row.archive_name)
        for row in Archive.select(Archive.project, Archive.revision, Archive.archive_name)
    }
    renames: list[tuple[Archive, str]] = []
    for row in Archive.select().where(Archive.archive_name.startswith(_ASPECT_PREFIX)):
        basename = Path(row.archive_name).name
        if ProjectFileTypes.of(basename).folder != MEDIA_DIR:
            continue
        new_name = f"{_MEDIA_PREFIX}{basename}"
        if (row.project_id, row.revision, new_name) in existing_names:
            logger.warning(
                "Media migration: left '%s' (project '%s', revision %s) in place — '%s' already exists there.",
                row.archive_name, row.project_id, row.revision, new_name,
            )
            continue
        renames.append((row, new_name))
    return renames
