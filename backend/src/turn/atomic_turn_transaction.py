from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence, TYPE_CHECKING

from db import Db, _utc_iso
from db.messages import _TIMESTAMP_UNSET, _group_user_fragments
from turn.turn_transaction import Inbox, PendingMessage, RowHandle, TurnTransaction, row_id

if TYPE_CHECKING:
    from tracking.actuators import TaskNamespace


class PendingTracking(RowHandle):
    __slots__ = (
        "session_id", "timestamp", "values", "env", "action_env", "local_memory", "output", "tool_calls",
        "old_state", "action", "new_state", "message", "origin", "position", "choice",
    )

    def __init__(self, session_id: int) -> None:
        super().__init__(None)
        self.session_id = session_id
        self.timestamp = datetime.utcnow()
        self.values: dict | None = None
        self.env: dict | None = None
        self.action_env: dict | None = None
        self.local_memory: dict | None = None
        self.output: dict | None = None
        self.tool_calls: list[dict] | None = None
        self.old_state: str | None = None
        self.action: str | None = None
        self.new_state: str | None = None
        self.message: RowHandle | None = None
        self.origin: str | None = None
        self.position: int | None = None
        self.choice: dict | None = None

    def insert(self, db: Db) -> None:
        raise NotImplementedError

    def is_evaluation_point(self) -> bool:
        return self.env is None and self.action_env is None and self.tool_calls is None and self.local_memory is None

    def is_real_transition(self) -> bool:
        return self.new_state is not None and self.old_state != self.new_state

    def as_signal(self) -> dict:
        return {
            'id': self.id, 'timestamp': _utc_iso(self.timestamp),
            'values': json.dumps(self.values) if self.values is not None else None,
            'output': json.dumps(self.output) if self.output else None,
            'expected_values': None, 'expected_state': None, 'comment': None,
            'old_state': self.old_state, 'action': self.action, 'new_state': self.new_state,
            'message_id': row_id(self.message), 'origin': self.origin,
            'position': self.position, 'choice': self.choice,
        }


class PendingTransition(PendingTracking):
    __slots__ = ("transition_log_level",)

    def __init__(
        self, session_id: int, old_state: str | None, action: str | None, new_state: str | None,
        transition_log_level: str, signal_values: dict | None, message: RowHandle | None, origin: str | None,
        output_values: dict | None,
    ) -> None:
        super().__init__(session_id)
        self.old_state, self.action, self.new_state = old_state, action, new_state
        self.transition_log_level = transition_log_level
        self.values, self.message, self.origin, self.output = signal_values, message, origin, output_values

    def insert(self, db: Db) -> None:
        self.id = db.save_transition(
            self.old_state, self.action, self.new_state, self.session_id,
            transition_log_level=self.transition_log_level, signal_values=self.values, message_id=row_id(self.message),
            origin=self.origin, output_values=self.output, timestamp=self.timestamp,
            position=self.position, choice=self.choice,
        )


class PendingSnapshot(PendingTracking):
    __slots__ = ()

    def __init__(self, session_id: int, values: dict | None, message: RowHandle | None, output_values: dict | None) -> None:
        super().__init__(session_id)
        self.values, self.message, self.output = values, message, output_values

    def insert(self, db: Db) -> None:
        self.id = db.save_signal_snapshot(
            self.values, self.session_id, row_id(self.message), output_values=self.output, timestamp=self.timestamp,
        )


class PendingEnv(PendingTracking):
    __slots__ = ()

    def __init__(self, session_id: int, env: dict, message: RowHandle | None) -> None:
        super().__init__(session_id)
        self.env, self.message = env, message

    def insert(self, db: Db) -> None:
        db.set_env(self.session_id, self.env or {}, message_id=row_id(self.message), timestamp=self.timestamp)


class PendingActionEnv(PendingTracking):
    __slots__ = ()

    def __init__(self, session_id: int, action_env: dict, origin: str | None) -> None:
        super().__init__(session_id)
        self.action_env, self.origin = action_env, origin

    def insert(self, db: Db) -> None:
        self.id = db.set_action_env(
            self.session_id, self.action_env or {}, origin=self.origin, message_id=row_id(self.message),
            timestamp=self.timestamp,
        )


class PendingLocalMemory(PendingTracking):
    __slots__ = ()

    def __init__(self, session_id: int, values: dict) -> None:
        super().__init__(session_id)
        self.local_memory = values

    def insert(self, db: Db) -> None:
        db.set_local_memory(self.session_id, self.local_memory or {}, timestamp=self.timestamp)


class PendingToolCalls(PendingTracking):
    __slots__ = ()

    def __init__(self, session_id: int, tool_calls: list[dict], message: RowHandle | None) -> None:
        super().__init__(session_id)
        self.tool_calls, self.message = tool_calls, message

    def insert(self, db: Db) -> None:
        self.id = db.record_tool_calls(
            self.session_id, self.tool_calls or [], message_id=row_id(self.message), timestamp=self.timestamp,
        )


def _latest(rows: list, until: datetime | None):
    within = [row for row in rows if until is None or row.timestamp <= until]
    return within[-1] if within else None


def _newest(*timestamps: datetime | None) -> datetime | None:
    return max(filter(None, timestamps), default=None)


class AtomicTurnTransaction(TurnTransaction):
    def __init__(self, db: Db, session_id: int, answering: Sequence[PendingMessage], inbox: Inbox) -> None:
        super().__init__(db, session_id, list(answering))
        self._inbox = inbox
        self._replies: list[PendingMessage] = []
        self._rows: list[PendingTracking] = []
        self._scheduled: list = []

    @property
    def answering(self) -> Sequence[PendingMessage]:
        return [m for m in self._answering if isinstance(m, PendingMessage)]

    def _messages(self) -> list[PendingMessage]:
        return [*self.answering, *self._replies]

    def commit(self) -> None:
        with self._db.atomic():
            for message in self._messages():
                message.land(self._db)
            for message in [m for m in self._messages() if m.answered_by is not None]:
                self._db.mark_messages_answered([message.id], row_id(message.answered_by) or 0)
            for row in self._rows:
                row.insert(self._db)
            for scheduled in self._scheduled:
                scheduled.run()
        self._inbox.forget(self._answering)

    def discard(self) -> None:
        self._inbox.forget(self._answering)

    def task_namespace(self, namespace: "TaskNamespace") -> "TaskNamespace":
        return namespace.deferring(self._scheduled)

    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None = None, reaction: str | None = None,
        timestamp: datetime | None | object = _TIMESTAMP_UNSET, tokens: int | None = None,
    ) -> PendingMessage:
        message = PendingMessage(role, content, session_id, datetime.utcnow(), audio_text=audio_text, tokens=tokens)
        message.reaction = reaction
        self._replies.append(message)
        return message

    def get_messages(self, session_id: int, last_n: int | None = None, since: datetime | None = None) -> list[dict]:
        persisted = self._db.get_messages(session_id, since=since)
        pending = [m.as_dict() for m in self._messages() if since is None or m.timestamp > since]
        combined = persisted + pending
        return combined[-last_n:] if last_n is not None else combined

    def get_turn_history(self, session_id: int, since: datetime | None, token_budget: int | None) -> list[dict]:
        history = self._db.get_turn_history(session_id, since, token_budget)
        fragments = _group_user_fragments([m.as_dict() for m in self.answering])
        return history + fragments

    def mark_messages_answered(self, messages: Sequence[RowHandle], assistant_message: RowHandle) -> None:
        for message in messages:
            _pending(message).answered_by = assistant_message

    def set_message_reaction(self, message: RowHandle, reaction: str | None) -> dict | None:
        _pending(message).reaction = reaction
        return _pending(message).as_dict()

    def set_message_tokens(self, message: RowHandle, tokens: int, cache_read_tokens: int = 0) -> None:
        _pending(message).tokens = tokens
        _pending(message).cache_read_tokens = cache_read_tokens

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        return self._db.has_messages_since(session_id, since) or any(
            since is None or m.timestamp > since for m in self._messages()
        )

    def has_assistant_message_since(self, session_id: int, since: datetime | None) -> bool:
        return self._db.has_assistant_message_since(session_id, since) or any(
            m.role == "assistant" and (since is None or m.timestamp > since) for m in self._messages()
        )

    def has_user_message(self, session_id: int) -> bool:
        return self._db.has_user_message(session_id) or any(m.role == "user" for m in self._messages())

    def record_tool_calls(
        self, session_id: int, tool_calls: list[dict], message_id: RowHandle | None = None,
        timestamp: datetime | None = None,
    ) -> PendingToolCalls:
        row = PendingToolCalls(session_id, tool_calls, message_id)
        self._rows.append(row)
        return row

    def link_tool_env_writes_to_message(
        self, session_id: int, message: RowHandle, since: datetime | None = None,
    ) -> None:
        for row in self._rows:
            unlinked_tool_write = (
                row.action_env is not None and row.origin == 'tool' and row.message is None
                and (since is None or row.timestamp >= since)
            )
            for _ in filter(None, [unlinked_tool_write]):
                row.message = message

    def save_transition(
        self, old_state: str | None, action: str | None, new_state: str | None, session_id: int,
        transition_log_level: str, signal_values: dict | None = None, message_id: RowHandle | None = None,
        origin: str | None = None, output_values: dict | None = None,
    ) -> PendingTransition:
        row = PendingTransition(
            session_id, old_state, action, new_state, transition_log_level, signal_values, message_id, origin,
            output_values,
        )
        self._rows.append(row)
        return row

    def save_signal_snapshot(
        self, values: dict | None, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> PendingSnapshot:
        row = PendingSnapshot(session_id, values, message_id, output_values)
        self._rows.append(row)
        return row

    def link_signal_to_message(self, signal_row: RowHandle, message: RowHandle) -> None:
        _pending_row(signal_row).message = message

    def place_action_entry(self, row: RowHandle, position: int, choice: dict | None) -> None:
        pending = _pending_row(row)
        pending.position, pending.choice = position, choice

    def get_signals(self, session_id: int) -> list[dict]:
        pending = [row.as_signal() for row in self._rows if row.is_evaluation_point()]
        return sorted(self._db.get_signals(session_id) + pending, key=lambda signal: signal['timestamp'])

    def _pending_signal_values(self, rows: list) -> dict | None:
        latest = _latest(rows, None)
        return dict(latest.values or {}) if latest is not None else None

    def get_latest_signal_snapshot(self, project_id: str) -> dict | None:
        pending = self._pending_signal_values([row for row in self._rows if row.values])
        return pending if pending is not None else self._db.get_latest_signal_snapshot(project_id)

    def get_latest_session_signal_snapshot(self, session_id: int) -> dict | None:
        pending = self._pending_signal_values(
            [row for row in self._rows if row.values and row.session_id == session_id]
        )
        return pending if pending is not None else self._db.get_latest_session_signal_snapshot(session_id)

    def get_last_transition_timestamp(self, project_id: str, until: datetime | None = None) -> datetime | None:
        latest = _latest([row for row in self._rows if row.is_real_transition()], until)
        return _newest(self._db.get_last_transition_timestamp(project_id, until=until), latest and latest.timestamp)

    def get_last_entry_timestamp_for_session(self, session_id: int, state_key: str) -> datetime | None:
        latest = _latest([row for row in self._rows if row.new_state == state_key], None)
        return _newest(self._db.get_last_entry_timestamp_for_session(session_id, state_key), latest and latest.timestamp)

    def get_current_state_for_session(self, session_id: int) -> str | None:
        latest = _latest([row for row in self._rows if row.new_state is not None], None)
        return latest.new_state if latest is not None else self._db.get_current_state_for_session(session_id)

    def history_cutoff_for_session(self, session_id: int, needs_cutoff: bool) -> datetime | None:
        latest = _latest([row for row in self._rows if row.is_real_transition()], None)
        cutoff = _newest(self._db.history_cutoff_for_session(session_id, needs_cutoff), latest and latest.timestamp)
        return cutoff if needs_cutoff else None

    def get_env(self, project_id: str, user: str, until: datetime | None = None) -> dict:
        latest = _latest([row for row in self._rows if row.env is not None], until)
        return dict(latest.env or {}) if latest is not None else self._db.get_env(project_id, user, until=until)

    def set_env(self, session_id: int, env: dict, message_id: RowHandle | None = None) -> None:
        self._rows.append(PendingEnv(session_id, env, message_id))

    def get_action_env(self, project_id: str, user: str, until: datetime | None = None) -> dict:
        latest = _latest([row for row in self._rows if row.action_env is not None], until)
        return dict(latest.action_env or {}) if latest is not None else self._db.get_action_env(project_id, user, until=until)

    def set_action_env(self, session_id: int, action_env: dict, origin: str | None = None) -> PendingActionEnv:
        row = PendingActionEnv(session_id, action_env, origin)
        self._rows.append(row)
        return row

    def get_local_memory(self, session_id: int, until: datetime | None = None) -> dict:
        latest = _latest([row for row in self._rows if row.local_memory is not None], until)
        return dict(latest.local_memory or {}) if latest is not None else self._db.get_local_memory(session_id, until=until)

    def set_local_memory(self, session_id: int, values: dict) -> None:
        self._rows.append(PendingLocalMemory(session_id, values))

    def clear_local_memory(self, session_id: int) -> None:
        for _ in filter(None, [self.get_local_memory(session_id)]):
            self._rows.append(PendingLocalMemory(session_id, {}))


def _pending(handle: RowHandle) -> PendingMessage:
    if not isinstance(handle, PendingMessage):
        raise RuntimeError(f"{handle!r} is not a message of this exchange.")
    return handle


def _pending_row(handle: RowHandle) -> PendingTracking:
    if not isinstance(handle, PendingTracking):
        raise RuntimeError(f"{handle!r} is not a tracking row of this exchange.")
    return handle
