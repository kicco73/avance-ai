"""One-off, boot-time data migration: every already-stored index.yml
revision that still calls the now-removed `source.<name>.read()` — see
tracking.sources.base.SourceDriver's own docstring, replaced by
`attachment.read(name)` — gets rewritten in place. Only an on-enter's own
`.read()` call has an equivalent (attachment.read is on-enter only); the
same call sitting in a trigger:/env: expression is left exactly as it
was — nothing to migrate it into (see AutomatonYamlEditor.
rewrite_legacy_source_read_calls). Every rewrite is proven safe before
being persisted: the rewritten index.yml is rebuilt through
AutomatonBuilder same as any real publish would, and only written back if
that still succeeds — anything that doesn't is logged and left untouched,
one revision's own trouble never blocking any other's."""
from __future__ import annotations

import re

from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from db import Db
from logging_factory import LoggerFactory

from .automaton_loader import AutomatonLoader
from .layout import ArchiveLayout

logger = LoggerFactory.get_logger(__name__)

_SOURCE_READ_CALL = re.compile(r"\bsource\.\w+\.read\(\)")


def migrate_legacy_source_read(db: Db) -> None:
    candidates = [
        (project_id, revision) for project_id, revision in db.list_index_yml_revisions()
        if _references_source_read(db, project_id, revision)
    ]
    if not candidates:
        return
    db.backup_now(f"migrating {len(candidates)} stored index.yml revision(s) off source.<name>.read()")
    automaton_loader = AutomatonLoader(db)
    migrated = skipped = 0
    for project_id, revision in candidates:
        try:
            if _migrate_one(db, automaton_loader, project_id, revision):
                migrated += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001 — one broken revision must never block the rest
            skipped += 1
            logger.error(
                "Project '%s', stored revision %s: couldn't migrate off source.<name>.read() — %s",
                project_id, revision, exc,
            )
    logger.warning(
        "source.<name>.read() migration done: %s revision(s) rewritten, %s left untouched (see any errors above).",
        migrated, skipped,
    )


def _references_source_read(db: Db, project_id: str, revision: int) -> bool:
    content = db.get_archive(project_id, "index.yml", revision=revision)
    return content is not None and bool(_SOURCE_READ_CALL.search(content.decode("utf-8", errors="replace")))


def _migrate_one(db: Db, automaton_loader: AutomatonLoader, project_id: str, revision: int) -> bool:
    archives = db.get_archives(project_id, revision=revision)
    decoded = ArchiveLayout.decode_text(archives)
    index_yml = decoded["index.yml"]
    assert isinstance(index_yml, str)

    editor = AutomatonYamlEditor(index_yml)
    unresolved = editor.rewrite_legacy_source_read_calls()
    if unresolved:
        logger.error(
            "Project '%s', stored revision %s: on-enter references source.%s.read() with no declared "
            "'avance:' url for that name — left untouched.",
            project_id, revision, ".read(), source.".join(sorted(unresolved)),
        )
        return False

    rewritten = editor.serialize()
    if rewritten == index_yml:
        # Every source.<name>.read() left in this revision lives in a
        # trigger:/env: expression, not on-enter — no equivalent there,
        # nothing this migration can do about it.
        return False

    decoded["index.yml"] = rewritten
    _, family, _ = AutomatonBuilder.read_declared_env_keys(rewritten)
    AutomatonBuilder().build(
        decoded, automaton_loader.known_projects_env_keys(project_id, family), legacy_project_id=project_id,
    )  # raises if the rewrite doesn't actually build — never persisted then

    db.write_archive_at_revision(project_id, "index.yml", revision, rewritten.encode("utf-8"), "text/plain")
    automaton_loader.invalidate(project_id, revision)
    return True
