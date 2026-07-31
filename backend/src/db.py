"""Single point of database access: only this module imports peewee/
playhouse or builds a query. No shared instance here — main.py reads
DATABASE_URL from the environment and constructs the one `Db(database_url)`
instance, passed explicitly to whatever needs it."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, Proxy, TextField, BlobField, CompositeKey, AutoField
from playhouse.db_url import connect, parse as parse_db_url

logger = logging.getLogger(__name__)

# Model classes below bind to this at class-definition time, since Peewee
# needs a Meta.database then — the real connection only exists once Db()
# is constructed (in main.py, with the URL it reads from the environment),
# at which point Db.__init__ calls database.initialize(...) to bind it.
database = Proxy()


class BaseModel(Model):
    class Meta:
        database = database


class ChatSession(BaseModel):
    """A chat conversation's lifetime for one user+project. Open/closed is
    computed by callers from `datetime_end` (see ChatSessionManager), not
    stored here. Named ChatSession (not Session) to stay distinct from
    session.py's process-local `Session` singleton — an unrelated concept."""
    id = AutoField()
    username = CharField()
    project_name = CharField()
    datetime_start = DateTimeField(null=False)
    datetime_end = DateTimeField(null=False)
    start_state = CharField(null=False)
    end_state = CharField(null=False)

    class Meta:
        indexes = (
            (("username", "project_name", "datetime_start", "datetime_end"), False),
            (("username", "project_name", "start_state", "end_state"), False),
        )


class Message(BaseModel):
    id = AutoField()
    role = CharField()  # "user" or "assistant"
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    audio_text = TextField(null=True)
    # Every message belongs to exactly one ChatSession (see
    # ChatSessionManager) — column name is "session_id" (Peewee's default
    # for a ForeignKeyField named `session`). project_name is reached via
    # session.project_name (a join) instead of being duplicated here.
    session = ForeignKeyField(ChatSession, null=False, backref="messages", on_delete="CASCADE")


# Single fixed user until there's real multi-user support (no login yet) —
# what every Settings row for "the current user" resolves to, internally,
# never accepted as a parameter from outside this module.
DEFAULT_USER = "user"


class Settings(BaseModel):
    """One row per user (today: always exactly one, DEFAULT_USER) — a
    current-value pointer, not a log. `project` is the active-project name
    (see set_active_project_name)."""
    user = CharField(primary_key=True)
    project = CharField()


class Signals(BaseModel):
    """Merges the old SignalSnapshot + Transition tables into one event
    log, scoped by `session` (its username/project_name are reached via a
    join, instead of being duplicated as separate columns/FKs here — see
    ChatSession). Every row is one of:
    - a plain auto-tracking evaluation: `values` set, transition fields
      all None (nothing fired).
    - a transition: old_state/action/new_state set. `values` is set when
      auto-tracking's own evaluation caused it, None for a manual action
      or the automaton's initial transition.
    A row can't reference "the snapshot that caused it" as a separate
    row anymore (there's no second row) — the evaluation and the
    transition it triggered are simply the same row."""
    id = AutoField()
    session = ForeignKeyField(ChatSession, null=False, backref="signals", on_delete="CASCADE")
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)  # JSON dict: {"problemRecognition": 42, ...}, or None
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)


class Archive(BaseModel):
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    content = BlobField(null=False)
    class Meta:
        primary_key = CompositeKey('project_name', 'archive_name')


class Db(object):
    # Sqlite files start with this fixed 16-byte header — checked before
    # restore_backup() ever touches the working file, so an unrelated/
    # corrupt upload fails loudly instead of clobbering it with garbage.
    _SQLITE_MAGIC = b"SQLite format 3\x00"

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        database.initialize(connect(database_url))
        database.connect(reuse_if_open=True)
        self._enable_foreign_keys()
        database.create_tables([ChatSession, Message, Settings, Signals, Archive], safe=True)

    @staticmethod
    def _enable_foreign_keys() -> None:
        """SQLite ignores FK constraints — including Message.session/
        Signals.session's ON DELETE CASCADE — unless this is set on the
        connection; it doesn't persist across reconnects, so
        restore_backup() re-applies it too."""
        database.execute_sql("PRAGMA foreign_keys = ON")

    def backup_file_path(self) -> str:
        """Absolute path of the working SQLite file backing this Db — the
        one location download/restore-backup ever act on (see
        config.database_url)."""
        return os.path.abspath(parse_db_url(self._database_url)["database"])

    def export_backup(self) -> bytes:
        with open(self.backup_file_path(), "rb") as f:
            return f.read()

    # The models this Db's schema is made of — the source of truth for
    # both create_tables (above) and the integrity check restore_backup
    # runs against an uploaded file (see _expected_schema/_check_schema).
    _MODELS = (ChatSession, Message, Settings, Signals, Archive)

    @classmethod
    def _expected_schema(cls) -> dict[str, set[str]]:
        """{table_name: {column_name, ...}} for the schema this code
        actually implements, derived from the models themselves so this
        can never drift out of sync with them."""
        return {
            model._meta.table_name: {field.column_name for field in model._meta.sorted_fields}
            for model in cls._MODELS
        }

    @staticmethod
    def _actual_schema(sqlite_path: str) -> dict[str, set[str]]:
        """Same shape as _expected_schema, read directly via sqlite3 (not
        peewee/the shared `database` Proxy) so this can inspect a
        candidate file without disturbing the live connection."""
        conn = sqlite3.connect(sqlite_path)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            return {
                table: {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}
                for table in tables
            }
        finally:
            conn.close()

    def _check_schema(self, sqlite_path: str) -> None:
        """Integrity check for restore_backup: the uploaded file must
        have the exact same tables and columns as the schema this code
        implements (not just a superset/subset) — column types/constraints
        aren't compared, just names, which is enough to catch a backup
        from an incompatible app version or an unrelated database."""
        expected = self._expected_schema()
        actual = self._actual_schema(sqlite_path)

        missing_tables = expected.keys() - actual.keys()
        extra_tables = actual.keys() - expected.keys()
        if missing_tables or extra_tables:
            raise ValueError(
                "Backup schema doesn't match: "
                + (f"missing table(s) {sorted(missing_tables)} " if missing_tables else "")
                + (f"unexpected table(s) {sorted(extra_tables)}" if extra_tables else "")
            )

        for table, columns in expected.items():
            missing_columns = columns - actual[table]
            extra_columns = actual[table] - columns
            if missing_columns or extra_columns:
                raise ValueError(
                    f"Backup schema doesn't match: table '{table}' "
                    + (f"missing column(s) {sorted(missing_columns)} " if missing_columns else "")
                    + (f"has unexpected column(s) {sorted(extra_columns)}" if extra_columns else "")
                )

    def restore_backup(self, content: bytes) -> None:
        """Replaces the working SQLite file with `content`, in place (same
        path/name), then reconnects. Callers must serialize this against
        in-flight chat turns themselves (see controller.py's /api/backup).
        Validated before the working file is touched at all: an invalid
        upload (wrong magic bytes or mismatched schema) leaves it exactly
        as it was."""
        if not content.startswith(self._SQLITE_MAGIC):
            raise ValueError("Uploaded file is not a valid SQLite database.")

        path = self.backup_file_path()
        # Written and validated alongside the target first — only renamed
        # into place once both the magic bytes and the schema check pass,
        # so a bad upload never touches the working file.
        tmp_path = f"{path}.restoring"
        with open(tmp_path, "wb") as f:
            f.write(content)
        try:
            self._check_schema(tmp_path)
        except Exception:
            os.remove(tmp_path)
            raise

        database.close()
        os.replace(tmp_path, path)
        database.connect(reuse_if_open=True)
        self._enable_foreign_keys()

    def create_chat_session(
        self,
        username: str,
        project_name: str,
        datetime_start: datetime,
        datetime_end: datetime,
        start_state: str,
        end_state: str,
    ) -> int:
        session = ChatSession.create(
            username=username,
            project_name=project_name,
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            start_state=start_state,
            end_state=end_state,
        )
        return session.id # type: ignore

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {
            "id": session.id,
            "username": session.username,
            "project_name": session.project_name,
            "datetime_start": session.datetime_start,
            "datetime_end": session.datetime_end,
            "start_state": session.start_state,
            "end_state": session.end_state,
        }

    def get_chat_session(self, session_id: int) -> dict | None:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    def get_latest_chat_session(self, username: str, project_name: str) -> dict | None:
        """The most recently started ChatSession for `username`+`project_name`
        (see ChatSessionManager: this is the only one writes may ever land
        on), or None if they have none yet."""
        session = (
            ChatSession.select()
            .where((ChatSession.username == username) & (ChatSession.project_name == project_name))
            .order_by(ChatSession.datetime_start.desc())
            .first()
        )
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(self, username: str, project_name: str) -> list[dict]:
        """All of `username`+`project_name`'s sessions, most recently
        started first — for a session picker (see ChatService.list_sessions)."""
        sessions = (
            ChatSession.select()
            .where((ChatSession.username == username) & (ChatSession.project_name == project_name))
            .order_by(ChatSession.datetime_start.desc())
        )
        return [self._chat_session_to_dict(s) for s in sessions]

    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str) -> None:
        ChatSession.update(datetime_end=datetime_end, end_state=end_state).where(
            ChatSession.id == session_id
        ).execute()

    def delete_chat_session(self, session_id: int) -> None:
        """Deletes `session_id` and everything scoped to it (its messages
        and signals). The schema's ON DELETE CASCADE (see Message.session/
        Signals.session, and _enable_foreign_keys) already covers this at
        the SQLite level — deleted explicitly here too, in dependency
        order, so correctness doesn't rest on that alone."""
        Signals.delete().where(Signals.session == session_id).execute()
        Message.delete().where(Message.session == session_id).execute()
        ChatSession.delete().where(ChatSession.id == session_id).execute()

    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None = None
    ) -> int:
        message = Message.create(role=role, content=content, session=session_id, audio_text=audio_text)
        return message.id # type: ignore

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def get_messages(
        self, session_id: int, last_n: int | None = None, since: datetime | None = None
    ) -> list[dict]:
        """Fetch `session_id`'s messages in chronological order. `last_n`
        limits at the SQL level (descending fetch + reverse) rather than
        slicing a full in-memory list. `since` excludes messages at or
        before that timestamp (a history_cutoff state's entry point)."""
        query = (
            Message.select()
            .where(Message.session == session_id)
            .order_by(Message.timestamp.desc())
        )
        if since is not None:
            query = query.where(Message.timestamp > since)
        if last_n is not None:
            query = query.limit(last_n)
        rows = list(query)
        rows.reverse()
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "audio_text": m.audio_text,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in rows
        ]

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        query = Message.select().where(Message.session == session_id)
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()

    def save_signal_snapshot(self, values: dict, session_id: int) -> int:
        row = Signals.create(session=session_id, values=json.dumps(values))
        return row.id

    def get_latest_signal_snapshot(self, project_name: str) -> dict | None:
        row = (
            Signals.select()
            .join(ChatSession, on=(Signals.session == ChatSession.id))
            .where((ChatSession.project_name == project_name) & Signals.values.is_null(False))
            .order_by(Signals.timestamp.desc())
            .first()
        )
        if row is None:
            return None
        return json.loads(row.values)

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
    ) -> None:
        Signals.create(
            session=session_id,
            old_state=old_state,
            action=action,
            new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
        )

        trigger_type = "auto" if signal_values is not None else "manual"
        level = getattr(logging, transition_log_level)
        message = f"State transition: {old_state} -> {new_state} (action={action}, trigger={trigger_type})"
        if signal_values:
            message += f" signals={signal_values}"
        logger.log(level, message)

    def _latest_transition(self, project_name: str, *, real_only: bool = False) -> Signals | None:
        query = (
            Signals.select()
            .join(ChatSession, on=(Signals.session == ChatSession.id))
            .where((ChatSession.project_name == project_name) & Signals.new_state.is_null(False))
        )
        if real_only:
            query = query.where(Signals.old_state != Signals.new_state)
        return query.order_by(Signals.timestamp.desc()).first()

    def get_current_state(self, project_name: str) -> str | None:
        transition = self._latest_transition(project_name)
        return transition.new_state if transition else None

    def get_last_transition_timestamp(self, project_name: str) -> datetime | None:
        transition = self._latest_transition(project_name, real_only=True)
        return transition.timestamp if transition else None

    def get_active_project_name(self, user: str = DEFAULT_USER) -> str | None:
        """The project name persisted for `user`, or None if Settings has no
        row for them yet (never populated — the very first boot)."""
        row = Settings.get_or_none(Settings.user == user)
        return row.project if row is not None else None

    def set_active_project_name(self, project_name: str, user: str = DEFAULT_USER) -> None:
        Settings.insert(user=user, project=project_name).on_conflict(
            conflict_target=[Settings.user],
            update={Settings.project: project_name},
        ).execute()

    def reset_project(self, project_name: str) -> None:
        """Wipes every user's sessions/messages/signals for `project_name`
        — used when the project itself is being deleted (see
        ProjectService.delete_project), where nothing should survive.
        For a single user's own "Reset conversation" action, see
        reset_project_for_user — this is deliberately not scoped by user."""
        # Signals/Message before ChatSession: both reference it by FK, and
        # neither carries project_name directly anymore (see Message/Signals).
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.project_name == project_name)
        Signals.delete().where(Signals.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.project_name == project_name).execute()

    def reset_project_for_user(self, username: str, project_name: str) -> None:
        """Wipes only `username`'s own sessions (all of them, open or
        closed) — plus their messages/signals — for `project_name`.
        Other users' data for the same project (if any) is untouched.
        Used by the "Reset conversation" action (see
        ProjectService.reset_active_project)."""
        session_ids = ChatSession.select(ChatSession.id).where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name)
        )
        Signals.delete().where(Signals.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name)
        ).execute()

    def reset_all(self) -> None:
        Signals.delete().execute()
        Message.delete().execute()
        ChatSession.delete().execute()

    def save_archive(self, project_name: str, archive_name: str, content: str) -> None:
        Archive.insert(
            project_name=project_name,
            archive_name=archive_name,
            content=content
        ).on_conflict(
            conflict_target=[Archive.project_name, Archive.archive_name],
            update={
                Archive.content: content
            }
        ).execute()

    
    def get_archive(self, project_name: str, archive_name: str) -> str:
        return Archive \
                .select(Archive.content) \
                .where(
                    (Archive.project_name == project_name) &
                    (Archive.archive_name == archive_name)
                ).scalar()

    def get_archives(self, project_name: str) -> dict:
        return {
            row.archive_name: row.content
            for row in (
                Archive
                    .select(Archive.archive_name, Archive.content)
                    .where(Archive.project_name == project_name)
            )
        }
    
    def list_projects(self) -> list[str]:
        return [
            p.project_name
            for p in Archive
                .select(Archive.project_name)
                .distinct()
        ]

    def list_archives(self, project_name: str) -> list[str]:
        return [
            p.archive_name
            for p in Archive
                .select(Archive.archive_name)
                .where(Archive.project_name == project_name)
        ]

    def delete_archive(self, project_name: str, archive_name: str) -> None:
        Archive.delete().where(
            (Archive.project_name == project_name) & 
            (Archive.archive_name == archive_name)
        ).execute()

    def delete_archives(self, project_name: str) -> None:
        Archive.delete().where(
            Archive.project_name == project_name
        ).execute()


