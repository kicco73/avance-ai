"""One-off, boot-time data migration for the test/live env-separation fix
(see chat.env_for_session's own docstring): before session type was ever
considered, a `Db.get_latest_chat_session` call with no type filter could
resolve to a test/preview session whenever no live session existed yet
for that (project, user) — attaching a test turn's own env/action_env
row to a session that was never live at all. Every such row is deleted
here, once.

Separately (and unrelated to *how* a row got there): a live session's own
action_env can still carry a key a project's *current* published revision
no longer declares under `env:` — left over from an earlier revision
(e.g. a since-removed `flight_record` key whose value kept growing every
turn). Cleaned up here too, once, for whatever's already stuck in that
state; chat.chat_service's own bootstrap-time cleanup handles it from
here on for every later revision change (see
ChatService._cleanup_orphan_action_env_keys).

Both are proven-safe, backed-up-first bulk rewrites (see Db.backup_now),
same shape as project.archive.legacy_source_read_migration.
"""
from __future__ import annotations

from db import Db
from db.models import ChatSession, Tracking
from logging_factory import LoggerFactory
from project.archive.automaton_loader import AutomatonLoader

logger = LoggerFactory.get_logger(__name__)

_EPHEMERAL_SESSION_TYPES = ("test", "preview")


def migrate_env_rows(db: Db) -> None:
    stray_row_ids = _stray_test_session_env_row_ids(db)
    orphans_by_pair = _orphan_action_env_keys(db)
    if not stray_row_ids and not orphans_by_pair:
        return
    db.backup_now(
        f"cleaning up {len(stray_row_ids)} test/preview-session env row(s) and "
        f"{len(orphans_by_pair)} live session env row(s) with orphaned action_env key(s)"
    )
    if stray_row_ids:
        Tracking.delete().where(Tracking.id.in_(stray_row_ids)).execute()
        logger.warning(
            "Env migration: deleted %s Tracking row(s) carrying env/action_env that belonged to a "
            "test/preview session — never a legitimate target for those (see chat.env_for_session).",
            len(stray_row_ids),
        )
    for (project_id, username), orphan_keys in orphans_by_pair.items():
        _drop_orphan_keys(db, project_id, username, orphan_keys)


def _stray_test_session_env_row_ids(db: Db) -> list[int]:
    rows = (
        Tracking
        .select(Tracking.id)
        .join(ChatSession, on=Tracking.session == ChatSession.id)
        .where(
            ChatSession.type.in_(_EPHEMERAL_SESSION_TYPES)
            & (Tracking.env.is_null(False) | Tracking.action_env.is_null(False))
        )
    )
    return [row.id for row in rows]


def _orphan_action_env_keys(db: Db) -> dict[tuple[str, str], set[str]]:
    """{(project_id, username): {orphan key, ...}} for every (project,
    user) pair with at least one live session whose current action_env
    carries a key the project's own current published revision no longer
    declares. A project never published at all has nothing to compare
    against, so it's skipped rather than treated as "declares nothing"."""
    automaton_loader = AutomatonLoader(db)
    result: dict[tuple[str, str], set[str]] = {}
    pairs = (
        ChatSession
        .select(ChatSession.project, ChatSession.username)
        .where(ChatSession.type == 'live')
        .distinct()
    )
    for row in pairs:
        project_id, username = row.project_id, row.username
        current = db.get_action_env(project_id, username)
        if not current:
            continue
        declared = _declared_env_keys(db, automaton_loader, project_id)
        if declared is None:
            continue
        orphans = set(current) - declared
        if orphans:
            result[(project_id, username)] = orphans
    return result


def _declared_env_keys(db: Db, automaton_loader: AutomatonLoader, project_id: str) -> set[str] | None:
    revision = db.get_project_published_revision(project_id)
    if revision is None:
        return None
    automaton = automaton_loader.load_at_revision(project_id, revision)
    return automaton.declared_env_key_names()


def _drop_orphan_keys(db: Db, project_id: str, username: str, orphan_keys: set[str]) -> None:
    session = db.get_latest_chat_session(username, project_id)
    if session is None:
        return
    current = db.get_action_env(project_id, username)
    cleaned = {key: value for key, value in current.items() if key not in orphan_keys}
    db.set_action_env(session["id"], cleaned)
    logger.warning(
        "Env migration: project '%s', user '%s': removed orphaned action_env key(s) %s — no longer "
        "declared by the published revision's own 'env' section.",
        project_id, username, sorted(orphan_keys),
    )
