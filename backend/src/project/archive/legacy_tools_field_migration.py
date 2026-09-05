"""One-off, boot-time data migration: every already-stored index.yml
revision whose state(s) still declare the now-removed `tools:` field —
see automaton.State's own ai_may_query_sources/ai_must_query_sources,
which replaced it — gets rewritten in place. `tools:` meant "the model
decides for itself whether to call one", exactly what `ai-may-query-sources`
means, so this is a pure key rename (AutomatonYamlEditor.
rewrite_legacy_tools_field), never a value change. Every rewrite is proven
safe before being persisted: the rewritten index.yml is rebuilt through
AutomatonBuilder same as any real publish would, and only written back if
that still succeeds — a revision whose `tools:`-listed source has no own
`ai-definition` yet (now required — see AutomatonBuilder's own
_actions_sanity_check) fails that rebuild and is left untouched instead,
logged for a human to fix by hand; one revision's own trouble never blocks
any other's. Same overall shape as legacy_source_read_migration.py."""
from __future__ import annotations

import re

from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from db import Db
from logging_factory import LoggerFactory

from .automaton_loader import AutomatonLoader
from .layout import ArchiveLayout

logger = LoggerFactory.get_logger(__name__)

_TOOLS_FIELD = re.compile(r"^\s*tools\s*:", re.MULTILINE)


def migrate_legacy_tools_field(db: Db) -> None:
    candidates = [
        (project_id, revision) for project_id, revision in db.list_index_yml_revisions()
        if _references_tools_field(db, project_id, revision)
    ]
    if not candidates:
        return
    db.backup_now(f"migrating {len(candidates)} stored index.yml revision(s) off 'tools:'")
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
                "Project '%s', stored revision %s: couldn't migrate off 'tools:' — %s",
                project_id, revision, exc,
            )
    logger.warning(
        "'tools:' field migration done: %s revision(s) rewritten, %s left untouched (see any errors above).",
        migrated, skipped,
    )


def _references_tools_field(db: Db, project_id: str, revision: int) -> bool:
    content = db.get_archive(project_id, "index.yml", revision=revision)
    return content is not None and bool(_TOOLS_FIELD.search(content.decode("utf-8", errors="replace")))


def _migrate_one(db: Db, automaton_loader: AutomatonLoader, project_id: str, revision: int) -> bool:
    archives = db.get_archives(project_id, revision=revision)
    decoded = ArchiveLayout.decode_text(archives)
    index_yml = decoded["index.yml"]
    assert isinstance(index_yml, str)

    editor = AutomatonYamlEditor(index_yml)
    if not editor.rewrite_legacy_tools_field():
        return False

    rewritten = editor.serialize()
    decoded["index.yml"] = rewritten
    _, family, _ = AutomatonBuilder.read_declared_env_keys(rewritten)
    AutomatonBuilder().build(
        decoded, automaton_loader.known_projects_env_keys(project_id, family), legacy_project_id=project_id,
    )  # raises if the rewrite doesn't actually build — never persisted then (e.g. a listed source with no ai-definition yet)

    db.write_archive_at_revision(project_id, "index.yml", revision, rewritten.encode("utf-8"), "text/plain")
    automaton_loader.invalidate(project_id, revision)
    return True
