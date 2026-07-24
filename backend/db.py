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
    def __init__(self, database_url: str) -> None:
        """Binds the module's database Proxy to `database_url`, opens the
        connection, and creates tables that don't exist yet, without
        touching existing data. Constructed once, in main.py."""
        database.initialize(connect(database_url))
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
        transition_log_level: str,
        signal_snapshot_id: int | None = None,
        signal_values: dict | None = None,
    ) -> None:
        """Persists the transition row and logs it at
        `transition_log_level` (the destination state's own configured
        level, e.g. from Automaton.get_state(new_state).transition_log_level)
        — the one place both happen, so they can't drift apart. Whether
        `signal_snapshot_id` is given (not just its value) decides "auto"
        vs "manual" in the log line, matching the column's own null
        semantics. `signal_values` is log-only detail (the full snapshot
        is already the persisted signal_snapshot row)."""
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

        trigger_type = "auto" if signal_snapshot_id is not None else "manual"
        level = getattr(logging, transition_log_level)
        message = f"State transition: {old_state} -> {new_state} (action={action}, trigger={trigger_type})"
        if signal_values:
            message += f" signals={signal_values}"
        logger.log(level, message)

    def get_current_state(self, model_name: str) -> str | None:
        """The state `model_name`'s latest Transition left it in, or None
        if there isn't one yet — callers fall back to the automaton's own
        initial_state themselves (`db.get_current_state(...) or
        automaton.initial_state`), since this module doesn't know it."""
        transition = (
            Transition.select()
            .where(Transition.model_name == model_name)
            .order_by(Transition.timestamp.desc())
            .first()
        )
        if transition is None:
            return None
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
