"""Single point of database access: only this module imports peewee/
playhouse or builds a query. No shared instance here — main.py reads
DATABASE_URL from the environment and constructs the one `Db(database_url)`
instance, passed explicitly to whatever needs it."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, Proxy, TextField
from playhouse.db_url import connect
from playhouse.migrate import SqliteMigrator, migrate

logger = logging.getLogger(__name__)

# Model classes below bind to this at class-definition time, since Peewee
# needs a Meta.database then — the real connection only exists once Db()
# is constructed (in main.py, with the URL it reads from the environment),
# at which point Db.__init__ calls database.initialize(...) to bind it.
database = Proxy()


class BaseModel(Model):
    class Meta:
        database = database


class Message(BaseModel):
    role = CharField()  # "user" or "assistant"
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    project_name = CharField(index=True, null=True)
    audio_text = TextField(null=True)


class SignalSnapshot(BaseModel):
    values = TextField()  # JSON dict: {"problemRecognition": 42, ...}
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    project_name = CharField(index=True, null=True)


# Single fixed user until there's real multi-user support (no login yet) —
# what every Settings/Transition row for "the current user" resolves to,
# internally, never accepted as a parameter from outside this module.
DEFAULT_USER = "user"


class Settings(BaseModel):
    """One row per user (today: always exactly one, DEFAULT_USER) — a
    current-value pointer, not a log. `project` is the active-project name
    (see set_active_project_name)."""
    user = CharField(primary_key=True)
    project = CharField()


class Transition(BaseModel):
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    old_state = CharField(index=True)
    action = CharField()
    new_state = CharField(index=True)
    # null => manual transition (button); not null => automatic, references
    # the signal snapshot whose values caused the transition.
    signal_snapshot = ForeignKeyField(SignalSnapshot, null=True, backref="transitions")
    # Always DEFAULT_USER for now (see Db.save_transition) — the column
    # exists ahead of real multi-user support so the schema won't need to
    # change again when that lands.
    user = ForeignKeyField(Settings, index=True, backref="transitions")
    project_name = CharField(index=True, null=True)


_PROJECT_SCOPED_TABLES = (Message, SignalSnapshot, Transition)


class Db(object):
    def __init__(self, database_url: str) -> None:
        database.initialize(connect(database_url))
        database.connect(reuse_if_open=True)
        database.create_tables([Message, SignalSnapshot, Settings, Transition], safe=True)
        self._migrate_add_project_name()
        self._migrate_add_audio_text()

    def _migrate_add_audio_text(self) -> None:
        """One-time, idempotent ALTER TABLE for databases created before
        Message.audio_text existed."""
        migrator = SqliteMigrator(database)
        columns = {c.name for c in database.get_columns(Message._meta.table_name)}
        if "audio_text" not in columns:
            migrate(migrator.add_column(Message._meta.table_name, "audio_text", TextField(null=True)))

    def _migrate_add_project_name(self) -> None:
        """One-time, idempotent migration to `project_name`/`project` (from
        the model->project vocabulary rename): renames the column from its
        pre-rename name (`model_name`/`model`) if present, else adds it
        fresh (nullable) for databases that predate the column entirely —
        backfilling only those freshly-added rows to whichever project is
        currently active, the closest available approximation (every
        switch used to wipe all data anyway, so pre-migration rows already
        all belonged to it)."""
        migrator = SqliteMigrator(database)
        freshly_added = []
        for table in _PROJECT_SCOPED_TABLES:
            columns = {c.name for c in database.get_columns(table._meta.table_name)}
            if "project_name" in columns:
                continue
            if "model_name" in columns:
                migrate(migrator.rename_column(table._meta.table_name, "model_name", "project_name"))
            else:
                migrate(migrator.add_column(table._meta.table_name, "project_name", CharField(index=True, null=True)))
                freshly_added.append(table)

        settings_columns = {c.name for c in database.get_columns(Settings._meta.table_name)}
        if "project" not in settings_columns and "model" in settings_columns:
            migrate(migrator.rename_column(Settings._meta.table_name, "model", "project"))

        if freshly_added:
            active_project_name = self.get_active_project_name() or "default"
            for table in freshly_added:
                table.update(project_name=active_project_name).where(table.project_name.is_null()).execute()

    def save_message(
        self, role: str, content: str, project_name: str, audio_text: str | None = None
    ) -> int:
        message = Message.create(role=role, content=content, project_name=project_name, audio_text=audio_text)
        return message.id

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def get_messages(
        self, project_name: str, last_n: int | None = None, since: datetime | None = None
    ) -> list[dict]:
        """Fetch `project_name`'s messages in chronological order. `last_n`
        limits at the SQL level (descending fetch + reverse) rather than
        slicing a full in-memory list. `since` excludes messages at or
        before that timestamp (a history_cutoff state's entry point)."""
        query = (
            Message.select()
            .where(Message.project_name == project_name)
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

    def has_messages_since(self, project_name: str, since: datetime | None) -> bool:
        query = Message.select().where(Message.project_name == project_name)
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()

    def save_signal_snapshot(self, values: dict, project_name: str) -> int:
        snapshot = SignalSnapshot.create(values=json.dumps(values), project_name=project_name)
        return snapshot.id

    def get_latest_signal_snapshot(self, project_name: str) -> dict | None:
        snapshot = (
            SignalSnapshot.select()
            .where(SignalSnapshot.project_name == project_name)
            .order_by(SignalSnapshot.timestamp.desc())
            .first()
        )
        if snapshot is None:
            return None
        return json.loads(snapshot.values)

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        project_name: str,
        transition_log_level: str,
        signal_snapshot_id: int | None = None,
        signal_values: dict | None = None,
    ) -> None:
        Transition.create(
            old_state=old_state,
            action=action,
            new_state=new_state,
            project_name=project_name,
            signal_snapshot=signal_snapshot_id,
            # Resolved here, not passed in: only one user exists today, but
            # the FK already exists so callers won't need to change later.
            user=DEFAULT_USER,
        )

        trigger_type = "auto" if signal_snapshot_id is not None else "manual"
        level = getattr(logging, transition_log_level)
        message = f"State transition: {old_state} -> {new_state} (action={action}, trigger={trigger_type})"
        if signal_values:
            message += f" signals={signal_values}"
        logger.log(level, message)

    def _latest_transition(self, project_name: str, *, real_only: bool = False) -> Transition | None:
        query = Transition.select().where(Transition.project_name == project_name)
        if real_only:
            query = query.where(Transition.old_state != Transition.new_state)
        return query.order_by(Transition.timestamp.desc()).first()

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
        Transition.delete().where(Transition.project_name == project_name).execute()
        SignalSnapshot.delete().where(SignalSnapshot.project_name == project_name).execute()
        Message.delete().where(Message.project_name == project_name).execute()

    def reset_all(self) -> None:
        Transition.delete().execute()
        SignalSnapshot.delete().execute()
        Message.delete().execute()
