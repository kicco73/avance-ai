"""Single point of database access: only this module imports peewee/
playhouse or builds a query. `db` below is the one shared instance,
already initialized (tables created) the moment this module is imported."""
from __future__ import annotations

import json
import os
from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, TextField
from playhouse.db_url import connect

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///avance.db")
database = connect(DATABASE_URL)


class BaseModel(Model):
    class Meta:
        database = database


class Message(BaseModel):
    role = CharField()  # "user" or "assistant"
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)


class SignalSnapshot(BaseModel):
    values = TextField()  # JSON dict: {"problemRecognition": 42, ...}
    timestamp = DateTimeField(index=True, default=datetime.utcnow)


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


class Db(object):
    def __init__(self) -> None:
        """Opens the connection and creates tables that don't exist yet,
        without touching existing data. Runs once, at import time — no
        other module needs to call anything to set this up."""
        database.connect(reuse_if_open=True)
        database.create_tables([Message, SignalSnapshot, Settings, Transition], safe=True)

    def save_message(self, role: str, content: str) -> int:
        message = Message.create(role=role, content=content)
        return message.id

    def get_messages(self, last_n: int | None = None) -> list[dict]:
        """Fetch messages in chronological order. `last_n` limits at the SQL
        level (descending fetch + reverse) rather than slicing a full
        in-memory list."""
        query = Message.select().order_by(Message.timestamp.desc())
        if last_n is not None:
            query = query.limit(last_n)
        rows = list(query)
        rows.reverse()
        return [
            {"role": m.role, "content": m.content, "timestamp": m.timestamp.isoformat()}
            for m in rows
        ]

    def is_empty(self) -> bool:
        """Cheap existence check: SQL EXISTS via Peewee's .exists(), rather
        than counting or fetching rows just to see if there are any."""
        return not Message.select().exists()

    def save_signal_snapshot(self, values: dict) -> int:
        snapshot = SignalSnapshot.create(values=json.dumps(values))
        return snapshot.id

    def get_latest_signal_snapshot(self) -> dict | None:
        snapshot = SignalSnapshot.select().order_by(SignalSnapshot.timestamp.desc()).first()
        if snapshot is None:
            return None
        return json.loads(snapshot.values)

    def save_transition(
        self, old_state: str, action: str, new_state: str, signal_snapshot_id: int | None
    ) -> None:
        Transition.create(
            old_state=old_state,
            action=action,
            new_state=new_state,
            signal_snapshot=signal_snapshot_id,
            # Resolved here, not passed in: only one user exists today, but
            # the FK already exists so callers won't need to change later.
            user=DEFAULT_USER,
        )

    def get_current_state(self, initial_state: str) -> str:
        transition = Transition.select().order_by(Transition.timestamp.desc()).first()
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

    def reset_all(self) -> None:
        """Empties Message/SignalSnapshot/Transition (DELETE, not DROP).
        Settings is deliberately untouched: the active model survives a
        reset, which only clears the conversation tied to it."""
        Transition.delete().execute()
        SignalSnapshot.delete().execute()
        Message.delete().execute()


# The one shared instance every other module imports (`from db import db`).
db = Db()
