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

from peewee import (
    BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, Model, Proxy, TextField, AutoField, fn
)
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
    # Expert-provided ground truth for benchmark_metrics (see
    # metrics_framework/benchmark_metrics) — the state an expert says the
    # automaton should be in immediately after this message. Set/cleared
    # only via ChatService.set_message_expected_state (the "Benchmark
    # project" view's States annotation — see Inspector.vue).
    expected_state = CharField(null=True)
    # Whether auto-tracking actually evaluates signals/state right after
    # this message — decided once, at chat-turn time, from the automaton's
    # own autotracking_on_user_message/autotracking_on_ai_message flags and
    # State.has_triggerable_actions (see ChatService._process_turn_locked)
    # — never recomputed later, since the project's own YAML (and so its
    # states/triggers) can change after the fact. Only a message with this
    # set to true has a linked Signals row to annotate (see
    # Signals.message) — the "Benchmark project" view's annotation
    # controls are hidden for every other message (see is_evaluation_point
    # in get_messages/get_message's own payload).
    is_evaluation_point = BooleanField(default=False)
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
    # Expert-provided ground truth for benchmark_metrics (see
    # metrics_framework/benchmark_metrics) — a JSON dict, like `values`, but
    # partial: only the signals the expert actually annotated. Set/cleared
    # only via ChatService.set_message_expected_signals (the "Benchmark
    # project" view's Signals annotation — see Inspector.vue). Only ever
    # meaningful on a row that has `message` set (see below) — never
    # written on a manual action's row, which has nothing to annotate
    # against.
    expected_values = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)
    # The message this row's evaluation ran right after — set only for a
    # row AutoTracker.run() produced (see ChatService._link_signal_to_message),
    # null for a manual action's transition (see project_service.
    # apply_manual_action) or the automaton's own initial transition (see
    # ChatService.open_if_needed), neither of which has a message to link
    # to. The one thing that lets an expected_values annotation resolve
    # back to "which message's evaluation is this" without guessing from
    # timestamps.
    message = ForeignKeyField(Message, null=True, backref="signal_row", on_delete="SET NULL")


class Archive(BaseModel):
    """One row per saved version of a project file — never updated in
    place, only ever inserted (see Db.save_project_version). `version` is
    project-wide, not per file: every file in a project is always saved
    together at the same version number, starting at 0 and always
    increasing by exactly 1 on the next real change — see
    Db.next_project_version, the single place that's decided. This is
    what lets a version number resolve to one complete, self-consistent
    snapshot across every file, with plain exact-match lookups (see
    Db.get_archive) instead of a "closest version" fallback."""
    id = AutoField()
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    version = IntegerField(index=True, null=False)
    # Always text (index.yml plus its .md/.txt/.csv attachments — see
    # project_service.TEXT_EDITABLE_EXTENSIONS): TextField so callers get
    # back a plain str, not the bytes a BlobField would hand back
    # regardless of what was written.
    content = TextField(null=False)

    class Meta:
        indexes = (
            (("project_name", "archive_name", "version"), True),
        )


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
        as it was.

        Reconnecting rebuilds the `database` Proxy's target from scratch
        (database.initialize(connect(...)), not a close()+connect() on
        the *same* underlying Database object — peewee's connection state
        is per-thread, so simply closing and reopening only fixes up
        whichever thread happens to call restore_backup(); every other
        thread that already holds one (a previous request handled on a
        different worker thread, or — should this ever grow into a
        multi-process/queue-consumer setup — a connection in a different
        process entirely) would keep its own stale connection to the
        file that just got replaced out from under it, which is exactly
        how this broke before (surfacing as `OperationalError: attempt to
        write a readonly database` on the next unrelated request). Handing
        the Proxy a brand new Database object sidesteps the assumption
        entirely: nothing needs reconnecting anywhere else, because any
        thread's *next* query goes through the new object and lazily
        opens its own fresh connection then, on first use — regardless of
        how many threads or processes are actually involved."""
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

        # A freshly written file gets whatever mode the process umask
        # allows, which isn't necessarily the working file's own mode —
        # preserved explicitly so a restore can never leave the file less
        # permissive than it was (e.g. missing the owner's write bit,
        # which every write after this would then fail against with
        # "attempt to write a readonly database"). Best-effort: if the
        # original file somehow can't be stat'd, proceed with whatever
        # mode the new file already has rather than fail the restore over it.
        try:
            os.chmod(tmp_path, os.stat(path).st_mode)
        except OSError:
            pass

        database.close()
        os.replace(tmp_path, path)
        database.initialize(connect(self._database_url))
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
        # is_evaluation_point starts (and, for most messages, stays) at its
        # column default of False — only link_signal_to_message ever flips
        # it, once it's known whether this specific message's processing
        # actually produced a Signals row to link (see its own docstring).
        message = Message.create(role=role, content=content, session=session_id, audio_text=audio_text)
        return message.id # type: ignore

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def get_message(self, message_id: int) -> dict | None:
        """A single message by id, with its `session_id` — for resolving
        "what timestamp does this message correspond to" (see the
        "Benchmark project" view's point-in-time Inspector,
        ChatService.get_metrics) and for ownership checks against its
        session (ChatService._require_own_message)."""
        message = Message.get_or_none(Message.id == message_id)
        if message is None:
            return None
        return {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "audio_text": message.audio_text,
            "timestamp": message.timestamp.isoformat(),
            "expected_state": message.expected_state,
            "is_evaluation_point": message.is_evaluation_point,
            "session_id": message.session_id,
        }

    def get_messages(
        self, session_id: int, last_n: int | None = None, since: datetime | None = None
    ) -> list[dict]:
        """Fetch `session_id`'s messages in chronological order. `last_n`
        limits at the SQL level (descending fetch + reverse) rather than
        slicing a full in-memory list. `since` excludes messages at or
        before that timestamp (a history_cutoff state's entry point).
        `session_id`/`expected_state`/`is_evaluation_point` are included on
        every row for metrics_framework/benchmark_metrics, which pools
        messages across several sessions and needs to know which one each
        came from, and for the "Benchmark project" view (see
        Message.is_evaluation_point)."""
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
                "expected_state": m.expected_state,
                "is_evaluation_point": m.is_evaluation_point,
                "session_id": session_id,
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

    def get_signals(self, session_id: int) -> list[dict]:
        """The full Signals event log for `session_id` — every row, snapshot
        or transition alike (see metrics_framework/README.md, which splits
        them back apart by `new_state`'s presence). Consumed by
        metrics_framework (via db_integration.py's AnalyticsDb protocol)
        and metrics_framework/benchmark_metrics, which additionally needs
        `expected_values` (expert ground truth, JSON-serialized the same
        way as `values` — see Signals.expected_values) and `message_id`
        (see Signals.message — the "Benchmark project" view's own way to
        find "the row this message's evaluation produced", with no
        timestamp-nearest guessing)."""
        rows = (
            Signals.select()
            .where(Signals.session == session_id)
            .order_by(Signals.timestamp.asc(), Signals.id.asc())
        )
        return [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "values": row.values,
                "expected_values": row.expected_values,
                "old_state": row.old_state,
                "action": row.action,
                "new_state": row.new_state,
                "message_id": row.message_id,
            }
            for row in rows
        ]

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
    ) -> int:
        row = Signals.create(
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
        return row.id

    def link_signal_to_message(self, signal_row_id: int, message_id: int) -> None:
        """Points an already-created Signals row (see save_signal_snapshot/
        save_transition) back at the message whose processing produced it,
        and flips that message's own is_evaluation_point to True — the
        only place either ever changes from their creation-time defaults.
        Called once auto-tracking has actually run for a message (see
        ChatService._run_auto_tracking) — never earlier: the user-message
        case's own evaluation runs before that message is even saved (see
        ChatService._process_turn_locked's evaluate-then-save order), so
        neither fact can be set at Signals.create()/save_message() time."""
        Signals.update(message=message_id).where(Signals.id == signal_row_id).execute()
        Message.update(is_evaluation_point=True).where(Message.id == message_id).execute()

    def get_signal_row_by_message(self, message_id: int) -> dict | None:
        """The Signals row `message_id`'s own evaluation produced (see
        Signals.message/link_signal_to_message), or None if this message
        isn't an evaluation point at all (see Message.is_evaluation_point).
        The "Benchmark project" view's own way to read/write a signals
        annotation for a given message without knowing its Signals row id."""
        row = Signals.get_or_none(Signals.message == message_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "timestamp": row.timestamp.isoformat(),
            "values": row.values,
            "expected_values": row.expected_values,
            "old_state": row.old_state,
            "action": row.action,
            "new_state": row.new_state,
            "message_id": row.message_id,
        }

    def set_message_expected_state(self, message_id: int, expected_state: str | None) -> None:
        """Sets (or, with None, clears) message_id's expected_state — see
        Message.expected_state's own docstring. Existence/ownership and
        "is this a valid state" are the caller's job (see
        ChatService.set_message_expected_state)."""
        Message.update(expected_state=expected_state).where(Message.id == message_id).execute()

    def set_signal_expected_values(self, signal_row_id: int, expected_values: dict | None) -> None:
        """Sets (or, with None/{}, clears) a Signals row's expected_values —
        see its own docstring. Existence/ownership and "are these real
        signal names" are the caller's job (see
        ChatService.set_message_expected_signals)."""
        serialized = json.dumps(expected_values) if expected_values else None
        Signals.update(expected_values=serialized).where(Signals.id == signal_row_id).execute()

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

    def next_project_version(self, project_name: str) -> int:
        """The version number `project_name`'s *next* save
        (save_project_version) will get: 0 if it has no archive rows at
        all yet, else one past its current highest — recomputed fresh
        from the stored rows every call, never a remembered counter. A
        read-only peek: a caller that needs to know this ahead of
        actually saving — e.g. to stamp it into index.yml's own content
        before handing it to save_project_version — calls this first;
        save_project_version itself always recomputes it independently
        rather than trusting a caller-supplied value, so there is no way
        to save at a stale or arbitrary version by mistake."""
        latest = Archive.select(fn.MAX(Archive.version)).where(Archive.project_name == project_name).scalar()
        return 0 if latest is None else latest + 1

    def save_project_version(self, project_name: str, files: dict[str, str]) -> int:
        """The only way to persist project files: every entry in `files`
        (every file in the project — changed content or simply carried
        forward as-is, see ProjectService._prepare_project_update) is
        written together as one new version — computed here, internally,
        via next_project_version, and nowhere else. There is deliberately
        no way for a caller to specify/target a version directly: a save
        can only ever create a brand new, current version for the whole
        project, never modify or add to an earlier one. Each file gets
        its own new row (Archive rows are never updated in place — see
        its own docstring). Returns the version used."""
        version = self.next_project_version(project_name)
        for archive_name, content in files.items():
            Archive.create(project_name=project_name, archive_name=archive_name, version=version, content=content)
        return version

    def get_archive(self, project_name: str, archive_name: str, version: int | None = None) -> tuple[str, int] | None:
        """(content, version) for `archive_name` — its latest version, or
        (when `version` is given) EXACTLY that version. None if no such
        row exists — either it was never saved at that version, or that
        version has since been pruned. No "highest not exceeding"
        fallback: every file that exists as of a given project version
        has a real row there (see save_project_version), so an exact
        match is always correct and a miss always means exactly what it
        says."""
        query = Archive.select(Archive.content, Archive.version).where(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name)
        )
        if version is not None:
            query = query.where(Archive.version == version)
        row = query.order_by(Archive.version.desc()).first()
        return (row.content, row.version) if row is not None else None

    def count_archive_versions(self, project_name: str, archive_name: str) -> int:
        return Archive.select().where(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name)
        ).count()

    def get_archives(self, project_name: str) -> dict:
        """Every archive's *latest* version content, keyed by name — what
        AutomatonBuilder/export_project_zip/the file explorer all treat
        as "the project's current files". Grouping by MAX(id) rather than
        MAX(version) is equivalent here (rows are only ever inserted, in
        increasing version order, so a later version always has a later
        id too) and avoids a second correlated subquery."""
        latest_ids = (
            Archive
                .select(fn.MAX(Archive.id))
                .where(Archive.project_name == project_name)
                .group_by(Archive.archive_name)
        )
        return {
            row.archive_name: row.content
            for row in Archive.select(Archive.archive_name, Archive.content).where(Archive.id.in_(latest_ids))
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
                .distinct()
        ]

    def prune_archive_history(self, project_name: str) -> None:
        """Keeps only each archive's latest version for `project_name`,
        deleting every older one — the version history is meant to be a
        bounded, session-scoped undo buffer (see the "Edit project" view's
        own cleanup on close), not permanent storage like the chat
        history."""
        latest_ids = (
            Archive
                .select(fn.MAX(Archive.id))
                .where(Archive.project_name == project_name)
                .group_by(Archive.archive_name)
        )
        Archive.delete().where(
            (Archive.project_name == project_name) & (Archive.id.not_in(latest_ids))
        ).execute()

    def delete_archive(self, project_name: str, archive_name: str) -> None:
        """Deletes every version of `archive_name` — the file itself is
        being removed from the project, so there's nothing left to keep
        history of."""
        Archive.delete().where(
            (Archive.project_name == project_name) &
            (Archive.archive_name == archive_name)
        ).execute()

    def delete_archives(self, project_name: str) -> None:
        Archive.delete().where(
            Archive.project_name == project_name
        ).execute()


