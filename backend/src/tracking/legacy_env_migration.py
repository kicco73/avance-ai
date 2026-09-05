from __future__ import annotations

from db import Db
from db.models import ChatSession, Tracking
from logging_factory import LoggerFactory
from project.archive.automaton_loader import AutomatonLoader

logger = LoggerFactory.get_logger(__name__)

_EPHEMERAL_SESSION_TYPES = ("test", "preview")


def migrate_env_rows(db: Db) -> None:
    stray_row_ids = _stray_test_session_env_row_ids(db)
    # Only a cheap read-only pre-check when there's nothing stray to
    # delete first — Db.get_action_env merges every session type for a
    # (project, user) pair, so a stray test/preview row still in place
    # could be mistaken for the live session's own latest value; once
    # `stray_row_ids` is non-empty this same call is redone for real,
    # below, only after that row is actually gone.
    if not stray_row_ids and not _orphan_action_env_keys(db):
        return
    db.backup_now(
        f"cleaning up {len(stray_row_ids)} test/preview-session env row(s) and any live session env "
        "row(s) left with an orphaned action_env key"
    )
    if stray_row_ids:
        Tracking.delete().where(Tracking.id.in_(stray_row_ids)).execute()
        logger.warning(
            "Env migration: deleted %s Tracking row(s) carrying env/action_env that belonged to a "
            "test/preview session.",
            len(stray_row_ids),
        )
    orphans_by_pair = _orphan_action_env_keys(db)
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
        try:
            current = db.get_action_env(project_id, username)
            if not current:
                continue
            declared = _declared_env_keys(db, automaton_loader, project_id)
            if declared is None:
                continue
            orphans = set(current) - declared
            if orphans:
                result[(project_id, username)] = orphans
        except Exception as exc:  # noqa: BLE001 — one project/user pair must never block the rest
            logger.error(
                "Env migration: project '%s', user '%s': couldn't check for orphaned action_env keys — %s",
                project_id, username, exc,
            )
    return result


def _declared_env_keys(db: Db, automaton_loader: AutomatonLoader, project_id: str) -> set[str] | None:
    revision = db.get_project_published_revision(project_id)
    if revision is None:
        return None
    try:
        automaton = automaton_loader.load_at_revision(project_id, revision)
    except (ValueError, FileNotFoundError) as exc:
        # A published revision that doesn't build is ProjectManager.
        # recompute_availability's own concern (it runs right after every
        # migration here, at boot) — this cleanup just skips the project
        # for now rather than taking the whole boot down with it; the
        # same orphan check runs again next boot, once it's fixed.
        logger.warning(
            "Env migration: project '%s', published revision %s no longer builds — %s. Skipping its "
            "own orphaned-action_env-key check for now.",
            project_id, revision, exc,
        )
        return None
    return automaton.declared_env_key_names()


def _drop_orphan_keys(db: Db, project_id: str, username: str, orphan_keys: set[str]) -> None:
    session = db.get_latest_chat_session(username, project_id)
    if session is None:
        return
    current = db.get_action_env(project_id, username)
    cleaned = {key: value for key, value in current.items() if key not in orphan_keys}
    db.set_action_env(session["id"], cleaned)
    logger.warning(
        "Env migration: project '%s', user '%s': removed orphaned action_env key(s) %s.",
        project_id, username, sorted(orphan_keys),
    )
