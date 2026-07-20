"""Single point of database access for the whole backend.

This is the only module allowed to import `peewee`/`playhouse` or build a
query: everything else (main.py, the /ws/chat handler, REST endpoints) goes
through the domain-named functions below. Kept synchronous/blocking on
purpose — fine for local SQLite. If this ever moves to a remote MySQL and
needs to become async, that change stays contained to this file precisely
because nothing else touches Peewee directly.
"""
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


class Transition(BaseModel):
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    old_state = CharField(index=True)
    action = CharField()
    new_state = CharField(index=True)
    # null => manual transition (button); not null => automatic, references
    # the signal snapshot whose values caused the transition.
    signal_snapshot = ForeignKeyField(SignalSnapshot, null=True, backref="transitions")


def init_db() -> None:
    """Call once at startup: opens the connection and creates tables that
    don't exist yet, without touching existing data."""
    database.connect(reuse_if_open=True)
    database.create_tables([Message, SignalSnapshot, Transition], safe=True)


def save_message(role: str, content: str) -> int:
    message = Message.create(role=role, content=content)
    return message.id


def get_all_messages() -> list[dict]:
    query = Message.select().order_by(Message.timestamp.asc())
    return [
        {"role": m.role, "content": m.content, "timestamp": m.timestamp.isoformat()}
        for m in query
    ]


def save_signal_snapshot(values: dict) -> int:
    snapshot = SignalSnapshot.create(values=json.dumps(values))
    return snapshot.id


def get_latest_signal_snapshot() -> dict | None:
    snapshot = SignalSnapshot.select().order_by(SignalSnapshot.timestamp.desc()).first()
    if snapshot is None:
        return None
    return json.loads(snapshot.values)


def save_transition(old_state: str, action: str, new_state: str, signal_snapshot_id: int | None) -> None:
    Transition.create(
        old_state=old_state,
        action=action,
        new_state=new_state,
        signal_snapshot=signal_snapshot_id,
    )


def get_current_state(initial_state: str) -> str:
    transition = Transition.select().order_by(Transition.timestamp.desc()).first()
    if transition is None:
        return initial_state
    return transition.new_state


def reset_all() -> None:
    """Empties all three tables (DELETE, not DROP — the schema stays)."""
    Transition.delete().execute()
    SignalSnapshot.delete().execute()
    Message.delete().execute()
