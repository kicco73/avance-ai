"""Single point of database access: only this module imports peewee/
playhouse or builds a query. No shared instance here — main.py reads
DATABASE_URL from the environment and constructs the one `Db(database_url)`
instance, passed explicitly to whatever needs it."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, Proxy, TextField, BlobField, CompositeKey, AutoField
from playhouse.db_url import connect

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
    project_name = CharField(index=True, null=True)
    audio_text = TextField(null=True)
    # Every message belongs to exactly one ChatSession (see
    # ChatSessionManager) — column name is "session_id" (Peewee's default
    # for a ForeignKeyField named `session`).
    session = ForeignKeyField(ChatSession, null=False, backref="messages")


class SignalSnapshot(BaseModel):
    id = AutoField()
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


class Archive(BaseModel):
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    content = BlobField(null=False)
    class Meta:
        primary_key = CompositeKey('project_name', 'archive_name')


class Db(object):
    def __init__(self, database_url: str) -> None:
        database.initialize(connect(database_url))
        database.connect(reuse_if_open=True)
        database.create_tables([ChatSession, Message, SignalSnapshot, Settings, Transition, Archive], safe=True)

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

    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str) -> None:
        ChatSession.update(datetime_end=datetime_end, end_state=end_state).where(
            ChatSession.id == session_id
        ).execute()

    def save_message(
        self, role: str, content: str, project_name: str, session_id: int, audio_text: str | None = None
    ) -> int:
        message = Message.create(
            role=role, content=content, project_name=project_name, session=session_id, audio_text=audio_text
        )
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
        # Message before ChatSession: Message.session references ChatSession.
        Message.delete().where(Message.project_name == project_name).execute()
        ChatSession.delete().where(ChatSession.project_name == project_name).execute()

    def reset_all(self) -> None:
        Transition.delete().execute()
        SignalSnapshot.delete().execute()
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


