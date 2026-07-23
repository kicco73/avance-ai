"""Single point of database access: only this module imports peewee/
playhouse or builds a query. `db` below is the one shared instance,
already initialized (tables created) the moment this module is imported."""
from __future__ import annotations

import json
import os
from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, TextField
from playhouse.db_url import connect
from playhouse.migrate import SqliteMigrator, migrate

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///avance.db")
database = connect(DATABASE_URL)


class BaseModel(Model):
    class Meta:
        database = database


class Message(BaseModel):
    role = CharField()  # "user" or "assistant"
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    model_name = CharField(index=True, null=True)


class SignalSnapshot(BaseModel):
    values = TextField()  # JSON dict: {"problemRecognition": 42, ...}
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    model_name = CharField(index=True, null=True)


# Single fixed user until there's real multi-user support (no login yet) —
# what every Settings/Transition row for "the current user" resolves to,
# internally, never accepted as a parameter from outside this module.
DEFAULT_USER = "user"


class Settings(BaseModel):
    """One row per user (today: always exactly one, DEFAULT_USER), upserted
    via set_active_model_name() — a current-value pointer, not a log."""
    user = CharField(primary_key=True)
    model = CharField()


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
    model_name = CharField(index=True, null=True)


_MODEL_SCOPED_TABLES = (Message, SignalSnapshot, Transition)


class Db(object):
    def __init__(self) -> None:
        """Opens the connection and creates tables that don't exist yet,
        without touching existing data. Runs once, at import time — no
        other module needs to call anything to set this up."""
        database.connect(reuse_if_open=True)
        database.create_tables([Message, SignalSnapshot, Settings, Transition], safe=True)
        self._migrate_add_model_name()

    def _migrate_add_model_name(self) -> None:
        """One-time, idempotent ALTER TABLE for databases created before
        model_name existed — adds the column (nullable) to each table it's
        missing from, then backfills existing rows to whichever model is
        currently active, the closest available approximation (every
        switch used to wipe all data anyway, so pre-migration rows already
        all belonged to it)."""
        migrator = SqliteMigrator(database)
        migrated = []
        for table in _MODEL_SCOPED_TABLES:
            columns = {c.name for c in database.get_columns(table._meta.table_name)}
            if "model_name" not in columns:
                migrate(migrator.add_column(table._meta.table_name, "model_name", CharField(index=True, null=True)))
                migrated.append(table)
        if migrated:
            active_model_name = self.get_active_model_name() or "default"
            for table in migrated:
                table.update(model_name=active_model_name).where(table.model_name.is_null()).execute()

    def save_message(self, role: str, content: str, model_name: str) -> int:
        message = Message.create(role=role, content=content, model_name=model_name)
        return message.id

    def get_messages(self, model_name: str, last_n: int | None = None) -> list[dict]:
        """Fetch `model_name`'s messages in chronological order. `last_n`
        limits at the SQL level (descending fetch + reverse) rather than
        slicing a full in-memory list."""
        query = (
            Message.select()
            .where(Message.model_name == model_name)
            .order_by(Message.timestamp.desc())
        )
        if last_n is not None:
            query = query.limit(last_n)
        rows = list(query)
        rows.reverse()
        return [
            {"role": m.role, "content": m.content, "timestamp": m.timestamp.isoformat()}
            for m in rows
        ]

    def is_empty(self, model_name: str) -> bool:
        """Cheap existence check: SQL EXISTS via Peewee's .exists(), rather
        than counting or fetching rows just to see if there are any."""
        return not Message.select().where(Message.model_name == model_name).exists()

    def save_signal_snapshot(self, values: dict, model_name: str) -> int:
        snapshot = SignalSnapshot.create(values=json.dumps(values), model_name=model_name)
        return snapshot.id

    def get_latest_signal_snapshot(self, model_name: str) -> dict | None:
        snapshot = (
            SignalSnapshot.select()
            .where(SignalSnapshot.model_name == model_name)
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
        model_name: str,
        signal_snapshot_id: int | None = None,
    ) -> None:
        Transition.create(
            old_state=old_state,
            action=action,
            new_state=new_state,
            model_name=model_name,
            signal_snapshot=signal_snapshot_id,
            # Resolved here, not passed in: only one user exists today, but
            # the FK already exists so callers won't need to change later.
            user=DEFAULT_USER,
        )

    def get_current_state(self, initial_state: str, model_name: str) -> str:
        transition = (
            Transition.select()
            .where(Transition.model_name == model_name)
            .order_by(Transition.timestamp.desc())
            .first()
        )
        if transition is None:
            return initial_state
        return transition.new_state

    def get_active_model_name(self, user: str = DEFAULT_USER) -> str | None:
        """The model name persisted for `user`, or None if Settings has no
        row for them yet (never populated — the very first boot)."""
        row = Settings.get_or_none(Settings.user == user)
        return row.model if row is not None else None

    def set_active_model_name(self, model_name: str, user: str = DEFAULT_USER) -> None:
        """Upserts `user`'s active-model pointer — REPLACE, not INSERT, so
        there's always at most one row per user rather than a history."""
        Settings.replace(user=user, model=model_name).execute()

    def reset_model(self, model_name: str) -> None:
        """Empties Message/SignalSnapshot/Transition rows for `model_name`
        only (filtered DELETE, not DROP) — used by POST /api/reset and
        DELETE /api/models/{model_name}, never touching other models'
        data. Settings is untouched, same as reset_all()."""
        Transition.delete().where(Transition.model_name == model_name).execute()
        SignalSnapshot.delete().where(SignalSnapshot.model_name == model_name).execute()
        Message.delete().where(Message.model_name == model_name).execute()

    def reset_all(self) -> None:
        """Empties Message/SignalSnapshot/Transition across every model
        (DELETE, not DROP). Settings is deliberately untouched. Low-level
        utility kept for other uses — no longer called by switch, manual
        reset, or the boot fallback, all of which are model-scoped now."""
        Transition.delete().execute()
        SignalSnapshot.delete().execute()
        Message.delete().execute()


# The one shared instance every other module imports (`from db import db`).
db = Db()
