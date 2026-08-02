"""Single point of database access: only this module imports peewee/
playhouse or builds a query. No shared instance here — main.py reads
DATABASE_URL from the environment and constructs the one `Db(database_url)`
instance, passed explicitly to whatever needs it."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from peewee import CharField, DateTimeField, ForeignKeyField, IntegerField, Model, Proxy, TextField, AutoField, fn
from playhouse.db_url import connect, parse as parse_db_url

logger = logging.getLogger(__name__)


def _utc_iso(dt: datetime) -> str:
    """Every DateTimeField in this module is written with
    `default=datetime.utcnow` — a naive datetime that's really always UTC,
    never the server's local time. `dt.isoformat()` alone would drop that
    fact on the floor, and a frontend `new Date(...)` parses a
    timezone-less ISO string as *local* time, not UTC — silently shifting
    every timestamp by the browser's own UTC offset. Stamping the
    timezone explicitly here is what lets the frontend be the only place
    that ever converts to the user's local time for display (see
    MessageBubble.vue's formatTimestamp)."""
    return dt.replace(tzinfo=timezone.utc).isoformat()

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
    - an env-memory update: only `env` set, everything else None — see
      Db.get_env/set_env/chat.env.Env. Never returned by get_signals (see
      its own docstring): it isn't a real evaluation or transition, just
      a place for the latest known env dict to live, scoped by session ->
      ChatSession the same way everything else here is.
    A row can't reference "the snapshot that caused it" as a separate
    row anymore (there's no second row) — the evaluation and the
    transition it triggered are simply the same row."""
    id = AutoField()
    session = ForeignKeyField(ChatSession, null=False, backref="signals", on_delete="CASCADE")
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)  # JSON dict: {"problemRecognition": 42, ...}, or None
    # JSON dict — see Db.get_env/set_env. Set only on a dedicated
    # env-memory row (see this class's own docstring), never alongside
    # `values`/old_state/action/new_state on the same row.
    env = TextField(null=True)
    # Expert-provided ground truth for benchmark_metrics (see
    # metrics_framework/benchmark_metrics) — the state an expert says the
    # automaton should be in immediately after this row's own evaluation.
    # Set/cleared only via ChatService.set_message_expected_state (the
    # "Label sessions" view's States annotation — see Inspector.vue).
    # Only ever meaningful on a row `message` (below) points somewhere —
    # never written on a manual action's row, which was never evaluated
    # and so has nothing to annotate against.
    expected_state = CharField(null=True)
    # Same idea, for signal values — a JSON dict, like `values`, but
    # partial: only the signals the expert actually annotated. Set/cleared
    # only via ChatService.set_message_expected_signals (the "Benchmark
    # project" view's Signals annotation).
    expected_values = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)
    # The message this row's evaluation ran right after — set at creation
    # time (see AutoTracker.run/ChatService._run_auto_tracking), never as
    # a later follow-up update: auto-tracking only ever runs once its own
    # triggering message is already saved (the user-message case explicitly
    # saves it first — see ChatService._process_turn_locked — precisely so
    # this can be known upfront). null for a manual action's transition
    # (see project_service.apply_manual_action) or the automaton's own
    # initial transition (see ChatService.open_if_needed), neither of
    # which was ever evaluated from a message at all. Equivalent to a
    # former Message.is_evaluation_point flag, but pointing at the actual
    # computation instead of just flagging its existence — the "Benchmark
    # project" view resolves annotatability for either a clicked message
    # or a clicked transition with the same lookup, straight off this
    # field (see get_signals' own `message_id`).
    message = ForeignKeyField(Message, null=True, backref="signal_row", on_delete="SET NULL")


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


class Archive(BaseModel):
    """One row per project file, holding only its current content — never
    a history (see History for that). Updated in place on every save/undo/
    redo (see Db.save_project_files/undo_project_file/redo_project_file),
    unlike the old version-stamped Archive this replaced. `revision` bumps
    on every content change; it exists only for optimistic-locking/future
    use, not as a historical index — there is no way to look up a past
    revision here."""
    id = AutoField()
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    revision = IntegerField(null=False, default=0)
    # Always text (index.yml plus its .md/.txt/.csv attachments — see
    # project_service.TEXT_EDITABLE_EXTENSIONS): TextField so callers get
    # back a plain str, not the bytes a BlobField would hand back
    # regardless of what was written.
    content = TextField(null=False)

    class Meta:
        indexes = (
            (("project_name", "archive_name"), True),
        )


class History(BaseModel):
    """Per-(user, project, file) undo/redo stacks — a bounded, session-
    scoped editing buffer (see Db.clear_history/ProjectService.
    clear_project_history, called on "Edit project"'s own Back), not
    permanent storage like Archive. Each row is one entry in either the
    undo stack (`kind="undo"`) or the redo stack (`kind="redo"`) for its
    own (user_id, project_name, archive_name); `seq` orders entries within
    that group, and the highest `seq` is always the top of that stack (see
    Db._push_history/_pop_history). `content` is the file content that
    entry would restore if popped, never the file's current content."""
    id = AutoField()
    user_id = CharField(index=True, null=False)
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)  # "undo" or "redo"
    seq = IntegerField(null=False)
    content = TextField(null=False)

    class Meta:
        indexes = (
            (("user_id", "project_name", "archive_name", "kind", "seq"), True),
        )


class Db(object):
    # Sqlite files start with this fixed 16-byte header — checked before
    # restore_backup() ever touches the working file, so an unrelated/
    # corrupt upload fails loudly instead of clobbering it with garbage.
    _SQLITE_MAGIC = b"SQLite format 3\x00"

    def __init__(self, database_url: str, force_drop_and_create_when_incompatible: bool = False) -> None:
        self._database_url = database_url
        database.initialize(connect(database_url))
        database.connect(reuse_if_open=True)
        self._enable_foreign_keys()
        if force_drop_and_create_when_incompatible:
            self._drop_and_recreate_if_incompatible()
        database.create_tables(self._MODELS, safe=True)

    def _drop_and_recreate_if_incompatible(self) -> None:
        """Only meaningful, and only ever called, when config.yml's own
        database.force-drop-and-create-when-incompatible is set (see
        config.AppConfig/main.py) — off by default, since it's
        destructive: every table on disk is dropped (not just the ones
        this code still has a model for) so the working file always ends
        up an exact match, then __init__'s own create_tables(safe=True)
        rebuilds them from scratch, empty.

        A no-op whenever there's nothing to be incompatible with — the
        working file doesn't exist yet (first boot) or has no tables at
        all — since create_tables(safe=True) alone already handles both
        of those on its own. Otherwise compares the file's actual schema
        against _expected_schema() the same way restore_backup's own
        _check_schema does; any difference (most commonly a renamed/
        removed column create_tables can't fix by itself — e.g. this
        version's Archive.revision replacing an older version's Archive.
        version) drops every table it found. Without this flag, the exact
        same mismatch is left exactly as today's code already leaves it —
        surfacing as a query error the first time something actually
        touches the incompatible table, not a startup crash."""
        path = self.backup_file_path()
        if not os.path.exists(path):
            return
        actual = self._actual_schema(path)
        if not actual:
            return
        if actual == self._expected_schema():
            return
        logger.warning(
            "Database schema at '%s' doesn't match what this code expects — dropping and "
            "recreating every table from scratch (database.force-drop-and-create-when-incompatible "
            "is enabled).",
            path,
        )
        for table in actual:
            database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')

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
    _MODELS = (ChatSession, Message, Settings, Signals, Archive, History)

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

    def get_latest_chat_session(
        self, username: str, project_name: str, until: datetime | None = None
    ) -> dict | None:
        """The most recently started ChatSession for `username`+`project_name`
        (see ChatSessionManager: this is the only one writes may ever land
        on), or None if they have none yet. `until` (naive-but-UTC, like
        every DateTimeField here) restricts to sessions that had already
        started at or before that point — see chat.env.Env's own
        point-in-time support."""
        query = ChatSession.select().where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name)
        )
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str, project_name: str, until: datetime | None = None
    ) -> list[dict]:
        """All of `username`+`project_name`'s sessions, most recently
        started first — for a session picker (see ChatService.
        list_sessions). `until`: see get_latest_chat_session's own."""
        query = ChatSession.select().where(
            (ChatSession.username == username) & (ChatSession.project_name == project_name)
        )
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        sessions = query.order_by(ChatSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

    def session_has_annotations(self, session_id: int) -> bool:
        """Whether session_id has at least one expert annotation
        (expected_state or expected_values) on any of its own Signals
        rows — see ChatService._session_payload's own `has_annotations`,
        for a single session."""
        return (
            Signals.select()
            .where(
                (Signals.session == session_id)
                & (Signals.expected_state.is_null(False) | Signals.expected_values.is_null(False))
            )
            .exists()
        )

    def get_annotated_session_ids(self, username: str, project_name: str) -> set[int]:
        """Which of username+project_name's own sessions have at least one
        expert annotation on any of their own Signals rows — one query
        for the whole list (see ChatService.list_sessions), rather than
        session_has_annotations called once per session."""
        rows = (
            Signals.select(Signals.session)
            .join(ChatSession, on=(Signals.session == ChatSession.id))
            .where(
                (ChatSession.username == username)
                & (ChatSession.project_name == project_name)
                & (Signals.expected_state.is_null(False) | Signals.expected_values.is_null(False))
            )
            .distinct()
        )
        return {row.session_id for row in rows}

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

    def truncate_session(self, session_id: int, cutoff: datetime) -> None:
        """"Restart from here" (see ChatService.truncate_session): deletes
        every Message/Signals row in `session_id` at/after `cutoff`.
        Nothing here needs to separately roll back "the current state" —
        get_current_state/_latest_transition already always resolve it
        fresh as the newest *surviving* Signals row's own new_state, so
        deleting the trailing rows *is* the rollback. Signals goes first
        (a surviving Message's own signal_row backref would otherwise
        dangle at the Python level between these two statements, even
        though the schema's ON DELETE SET NULL — see Signals.message —
        would leave it consistent either way).

        Guards against deleting the automaton's own one-time
        "" -> start_state bookkeeping row (see ChatService.open_if_needed)
        even if its timestamp happens to fall at/after cutoff: that row is
        scoped to the *project* (created once, on its literal first
        session, never per-session), so deleting it would make
        get_current_state(project_name) fall back to "never initialized"
        for the whole project, not just roll this one session back."""
        (
            Signals.delete()
            .where(
                (Signals.session == session_id)
                & (Signals.timestamp >= cutoff)
                & (Signals.old_state.is_null(True) | (Signals.old_state != ""))
            )
            .execute()
        )
        Message.delete().where((Message.session == session_id) & (Message.timestamp >= cutoff)).execute()

    def latest_message_or_signal_timestamp(self, session_id: int) -> datetime | None:
        """The most recent timestamp still on record for `session_id`,
        across both Message and Signals — used to roll ChatSession.
        datetime_end back in step after truncate_session, so a session
        that's just been cut back doesn't keep reporting "last active" at
        a moment that no longer has anything behind it. None if the
        session now has nothing left at all (cut back to before its own
        first message)."""
        latest_message = (
            Message.select(fn.MAX(Message.timestamp)).where(Message.session == session_id).scalar()
        )
        latest_signal = (
            Signals.select(fn.MAX(Signals.timestamp)).where(Signals.session == session_id).scalar()
        )
        candidates = [t for t in (latest_message, latest_signal) if t is not None]
        return max(candidates) if candidates else None

    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None = None
    ) -> int:
        message = Message.create(role=role, content=content, session=session_id, audio_text=audio_text)
        return message.id # type: ignore

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def get_message(self, message_id: int) -> dict | None:
        """A single message by id, with its `session_id` — for resolving
        "what timestamp does this message correspond to" (see the
        "Label sessions" view's point-in-time Inspector,
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
            "timestamp": _utc_iso(message.timestamp),
            "session_id": message.session_id,
        }

    def get_messages(
        self, session_id: int, last_n: int | None = None, since: datetime | None = None
    ) -> list[dict]:
        """Fetch `session_id`'s messages in chronological order. `last_n`
        limits at the SQL level (descending fetch + reverse) rather than
        slicing a full in-memory list. `since` excludes messages at or
        before that timestamp (a history_cutoff state's entry point).
        `session_id` is included on every row for metrics_framework/
        benchmark_metrics, which pools messages across several sessions and
        needs to know which one each came from. Whether/what a message was
        annotated with lives on its own Signals row instead (see
        get_signals' own `message_id`), not here."""
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
                "timestamp": _utc_iso(m.timestamp),
                "session_id": session_id,
            }
            for m in rows
        ]

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        query = Message.select().where(Message.session == session_id)
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None = None) -> int:
        row = Signals.create(session=session_id, values=json.dumps(values), message=message_id)
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
        metrics_framework (via metrics_framework/interfaces.py's
        AnalyticsDb protocol) and metrics_framework/benchmark_metrics,
        which additionally needs
        `expected_values` (expert ground truth, JSON-serialized the same
        way as `values` — see Signals.expected_values) and `message_id`
        (see Signals.message — the "Label sessions" view's own way to
        find "the row this message's evaluation produced", with no
        timestamp-nearest guessing). Excludes env-only rows (see Signals'
        own docstring, get_env) — they're internal bookkeeping, never a
        real evaluation or transition, and would satisfy neither of the
        two shapes every caller here already assumes."""
        rows = (
            Signals.select()
            .where((Signals.session == session_id) & Signals.env.is_null(True))
            .order_by(Signals.timestamp.asc(), Signals.id.asc())
        )
        return [
            {
                "id": row.id,
                "timestamp": _utc_iso(row.timestamp),
                "values": row.values,
                "expected_values": row.expected_values,
                "expected_state": row.expected_state,
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
        message_id: int | None = None,
    ) -> int:
        row = Signals.create(
            session=session_id,
            old_state=old_state,
            action=action,
            new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
            message=message_id,
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
        save_transition) back at the message whose processing produced it
        — called once that message itself has an id (see
        ChatService._run_auto_tracking): the user-message case's own
        evaluation runs *before* that message is saved (unlike the
        ai-message case, which saves its own message first and so could
        set `message` directly at creation instead — this follow-up update
        is used uniformly for both anyway, one mechanism rather than two)."""
        Signals.update(message=message_id).where(Signals.id == signal_row_id).execute()

    def get_signal_row_by_message(self, message_id: int) -> dict | None:
        """The Signals row `message_id`'s own evaluation produced (see
        Signals.message), or None if this message isn't an evaluation
        point at all. The "Label sessions" view's own way to read/write
        a signals annotation for a given message without knowing its
        Signals row id."""
        row = Signals.get_or_none(Signals.message == message_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "timestamp": _utc_iso(row.timestamp),
            "values": row.values,
            "expected_values": row.expected_values,
            "expected_state": row.expected_state,
            "old_state": row.old_state,
            "action": row.action,
            "new_state": row.new_state,
            "message_id": row.message_id,
        }

    def set_signal_expected_state(self, signal_row_id: int, expected_state: str | None) -> None:
        """Sets (or, with None, clears) a Signals row's expected_state —
        see its own docstring. Existence/ownership and "is this a valid
        state" are the caller's job (see
        ChatService.set_message_expected_state)."""
        Signals.update(expected_state=expected_state).where(Signals.id == signal_row_id).execute()

    def set_signal_expected_values(self, signal_row_id: int, expected_values: dict | None) -> None:
        """Sets (or, with None/{}, clears) a Signals row's expected_values —
        see its own docstring. Existence/ownership and "are these real
        signal names" are the caller's job (see
        ChatService.set_message_expected_signals)."""
        serialized = json.dumps(expected_values) if expected_values else None
        Signals.update(expected_values=serialized).where(Signals.id == signal_row_id).execute()

    def delete_signal_row(self, signal_row_id: int) -> None:
        """Deletes one Signals row outright — used only for a session-start
        bookkeeping row (old_state == "", see ChatService.
        _materialize_session_start_row) once its only reason to exist, an
        expert annotation, is cleared again (see
        ChatService._finalize_annotation_write). Never used on a row with
        real observed `values` — those stay forever, annotated or not."""
        Signals.delete().where(Signals.id == signal_row_id).execute()

    def clear_session_annotations(self, session_id: int) -> None:
        """Clears every expected_state/expected_values annotation across
        session_id's own Signals rows in one statement — the "Benchmark
        project" view's "Unlabel all" action (see ChatService.
        clear_session_annotations), after its own confirmation dialog.
        Ownership is the caller's job, same as every other annotation
        write here. Also deletes any session-start bookkeeping row
        (old_state == "", see ChatService._materialize_session_start_row)
        this leaves fully empty — same reasoning as delete_signal_row,
        just applied session-wide instead of message-by-message."""
        Signals.update(expected_state=None, expected_values=None).where(Signals.session == session_id).execute()
        Signals.delete().where((Signals.session == session_id) & (Signals.old_state == "")).execute()

    def _latest_transition(
        self, project_name: str, *, real_only: bool = False, until: datetime | None = None
    ) -> Signals | None:
        query = (
            Signals.select()
            .join(ChatSession, on=(Signals.session == ChatSession.id))
            .where((ChatSession.project_name == project_name) & Signals.new_state.is_null(False))
        )
        if real_only:
            query = query.where(Signals.old_state != Signals.new_state)
        if until is not None:
            query = query.where(Signals.timestamp <= until)
        return query.order_by(Signals.timestamp.desc()).first()

    def get_current_state(self, project_name: str) -> str | None:
        transition = self._latest_transition(project_name)
        return transition.new_state if transition else None

    def get_last_transition_timestamp(self, project_name: str, until: datetime | None = None) -> datetime | None:
        transition = self._latest_transition(project_name, real_only=True, until=until)
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

    def clear_active_project_name(self, user: str = DEFAULT_USER) -> None:
        """Deletes `user`'s own Settings row outright, rather than leaving
        `project` pointing at a name that no longer exists (see
        ProjectService.delete_project, when the deleted project was the
        active one and nothing was left to fall back to) — reuses the
        exact same "no Settings row" case get_active_project_name already
        treats as None, instead of Settings.project needing to become
        nullable just for this one case."""
        Settings.delete().where(Settings.user == user).execute()

    def get_env(
        self, project_name: str, user: str = DEFAULT_USER, until: datetime | None = None
    ) -> dict:
        """The latest known "environment" dict (see chat.env.Env) for
        (user, project_name) — read off whichever of their own Signals
        rows for this project most recently recorded one (see set_env's
        own dedicated env-only rows, and Signals' own docstring). {} if
        none exists yet. Scoped by session -> ChatSession like everything
        else in Signals: deleting a session (a full "Reset conversation",
        or the whole project) deletes its own env rows right along with
        it, same as any other Signals row for that session. `until`
        (naive-but-UTC): restricts to env rows recorded at or before that
        point — the "Label sessions" view's point-in-time Inspector."""
        query = (
            Signals.select(Signals.env)
            .join(ChatSession, on=(Signals.session == ChatSession.id))
            .where(
                (ChatSession.project_name == project_name)
                & (ChatSession.username == user)
                & Signals.env.is_null(False)
            )
        )
        if until is not None:
            query = query.where(Signals.timestamp <= until)
        row = query.order_by(Signals.timestamp.desc()).first()
        return json.loads(row.env) if row is not None else {}

    def set_env(self, project_name: str, env: dict, user: str = DEFAULT_USER) -> None:
        """Inserts a new env-only Signals row (see its own docstring)
        under (user, project_name)'s latest chat session — a no-op if
        they have no session at all yet (env is only ever set from
        within an active chat turn, which always has one by then)."""
        session = self.get_latest_chat_session(user, project_name)
        if session is None:
            return
        Signals.create(session=session["id"], env=json.dumps(env))

    def reset_project(self, project_name: str) -> None:
        """Wipes every user's sessions/messages/signals (env included —
        see Signals' own docstring) for `project_name` — used when the
        project itself is being deleted (see ProjectService.
        delete_project), where nothing should survive. For a single
        user's own "Reset conversation" action, see
        reset_project_for_user — this is deliberately not scoped by user."""
        # Signals/Message before ChatSession: both reference it by FK, and
        # neither carries project_name directly anymore (see Message/Signals).
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.project_name == project_name)
        Signals.delete().where(Signals.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.project_name == project_name).execute()

    def reset_project_for_user(self, username: str, project_name: str) -> None:
        """Wipes only `username`'s own sessions (all of them, open or
        closed) — plus their messages/signals/env (see Signals' own
        docstring: an env row is scoped to a session like any other, so
        it can't outlive one) — for `project_name`. Other users' data for
        the same project (if any) is untouched. Used by the "Reset
        conversation" action (see ProjectService.reset_active_project)."""
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

    def get_archive(self, project_name: str, archive_name: str) -> str | None:
        """`archive_name`'s current content, or None if it doesn't exist
        (in this project, or at all)."""
        row = Archive.get_or_none(
            (Archive.project_name == project_name) & (Archive.archive_name == archive_name)
        )
        return row.content if row is not None else None

    def get_archives(self, project_name: str) -> dict:
        """Every file's current content, keyed by name — what
        AutomatonBuilder/export_project_zip/the file explorer all treat as
        "the project's current files"."""
        return {
            row.archive_name: row.content
            for row in Archive.select(Archive.archive_name, Archive.content).where(
                Archive.project_name == project_name
            )
        }

    def save_project_files(self, project_name: str, files: dict[str, str]) -> None:
        """Upserts each entry in `files` as its own file's new current
        content, bumping `revision` — in place, never a new row (see
        Archive's own docstring). No History side effect: this is the bulk
        path (project upload/replace, see ProjectService.put_project),
        distinct from save_project_file's own single-file undo/redo
        bookkeeping."""
        for archive_name, content in files.items():
            existing = Archive.get_or_none(
                (Archive.project_name == project_name) & (Archive.archive_name == archive_name)
            )
            if existing is None:
                Archive.create(project_name=project_name, archive_name=archive_name, revision=0, content=content)
            else:
                Archive.update(content=content, revision=Archive.revision + 1).where(
                    Archive.id == existing.id
                ).execute()

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
        """Removes `archive_name` from the project along with every user's
        own undo/redo history for it — the file itself is gone, so there's
        nothing left to undo/redo back to."""
        Archive.delete().where(
            (Archive.project_name == project_name) &
            (Archive.archive_name == archive_name)
        ).execute()
        History.delete().where(
            (History.project_name == project_name) & (History.archive_name == archive_name)
        ).execute()

    def delete_archives(self, project_name: str) -> None:
        Archive.delete().where(
            Archive.project_name == project_name
        ).execute()
        History.delete().where(
            History.project_name == project_name
        ).execute()

    # --- Undo/redo history --------------------------------------------
    #
    # Two stacks per (user, project, file): `undo` holds content to
    # restore going backward, `redo` holds content to restore going
    # forward (see History's own docstring). save_project_file/
    # undo_project_file/redo_project_file below are the only entry points
    # meant to be used from outside this section — they keep Archive's
    # current content and these two stacks consistent with each other.

    def _next_history_seq(self, user_id: str, project_name: str, archive_name: str, kind: str) -> int:
        latest = (
            History.select(fn.MAX(History.seq))
            .where(
                (History.user_id == user_id)
                & (History.project_name == project_name)
                & (History.archive_name == archive_name)
                & (History.kind == kind)
            )
            .scalar()
        )
        return 0 if latest is None else latest + 1

    def _push_history(self, user_id: str, project_name: str, archive_name: str, kind: str, content: str) -> None:
        seq = self._next_history_seq(user_id, project_name, archive_name, kind)
        History.create(
            user_id=user_id, project_name=project_name, archive_name=archive_name,
            kind=kind, seq=seq, content=content,
        )

    def _pop_history(self, user_id: str, project_name: str, archive_name: str, kind: str) -> str | None:
        row = (
            History.select()
            .where(
                (History.user_id == user_id)
                & (History.project_name == project_name)
                & (History.archive_name == archive_name)
                & (History.kind == kind)
            )
            .order_by(History.seq.desc())
            .first()
        )
        if row is None:
            return None
        content = row.content
        row.delete_instance()
        return content

    def _clear_history_kind(self, user_id: str, project_name: str, archive_name: str, kind: str) -> None:
        History.delete().where(
            (History.user_id == user_id)
            & (History.project_name == project_name)
            & (History.archive_name == archive_name)
            & (History.kind == kind)
        ).execute()

    def has_undo(self, user_id: str, project_name: str, archive_name: str) -> bool:
        return History.select().where(
            (History.user_id == user_id)
            & (History.project_name == project_name)
            & (History.archive_name == archive_name)
            & (History.kind == "undo")
        ).exists()

    def has_redo(self, user_id: str, project_name: str, archive_name: str) -> bool:
        return History.select().where(
            (History.user_id == user_id)
            & (History.project_name == project_name)
            & (History.archive_name == archive_name)
            & (History.kind == "redo")
        ).exists()

    def save_project_file(self, user_id: str, project_name: str, archive_name: str, content: str) -> None:
        """The single-file edit path (see ProjectService.put_project_file):
        pushes `archive_name`'s previous content onto `user_id`'s own undo
        stack (skipped if this is the file's first-ever save — nothing to
        undo back to yet), clears their redo stack (a normal edit
        invalidates whatever redo could have replayed), then makes
        `content` the file's new current content. The only one of the
        three that ever touches Archive — undo/redo (below) are a pure
        editor preview, never persisted on their own."""
        previous = self.get_archive(project_name, archive_name)
        if previous is not None:
            self._push_history(user_id, project_name, archive_name, "undo", previous)
        self._clear_history_kind(user_id, project_name, archive_name, "redo")
        self.save_project_files(project_name, {archive_name: content})

    def undo_project_file(self, user_id: str, project_name: str, archive_name: str, current_content: str) -> str | None:
        """Pops `user_id`'s own most recent undo entry for `archive_name`
        and pushes `current_content` — whatever the editor is showing
        right now, supplied by the caller (see ProjectService.
        undo_project_file), not necessarily Archive's own content — onto
        their redo stack, so a later redo can bring it back. Returns the
        popped content, or None (no-op, nothing changed) if their undo
        stack is empty. Never touches Archive: undoing only changes what
        the editor previews, never what's actually saved."""
        previous = self._pop_history(user_id, project_name, archive_name, "undo")
        if previous is None:
            return None
        self._push_history(user_id, project_name, archive_name, "redo", current_content)
        return previous

    def redo_project_file(self, user_id: str, project_name: str, archive_name: str, current_content: str) -> str | None:
        """Mirror of undo_project_file, replaying `user_id`'s own redo
        stack instead."""
        next_content = self._pop_history(user_id, project_name, archive_name, "redo")
        if next_content is None:
            return None
        self._push_history(user_id, project_name, archive_name, "undo", current_content)
        return next_content

    def clear_history(self, user_id: str, project_name: str) -> None:
        """Deletes `user_id`'s own undo/redo history for every file in
        `project_name` — see ProjectService.clear_project_history, called
        when the "Edit project" view is opened, so a fresh editing session
        never inherits a previous one's undo/redo trail."""
        History.delete().where(
            (History.user_id == user_id) & (History.project_name == project_name)
        ).execute()


