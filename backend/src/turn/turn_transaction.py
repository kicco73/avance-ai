from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Protocol

from db import Db
from db.messages import _TIMESTAMP_UNSET

OPENING_USER_TURN = {"role": "user", "content": "..."}


class RowHandle(object):
    __slots__ = ("id",)

    def __init__(self, id: int | None = None) -> None:
        self.id = id

    def __repr__(self) -> str:
        return f"RowHandle({self.id})"


def row_id(handle: RowHandle | None) -> int | None:
    return handle.id if handle is not None else None


class TurnDbInterface(Protocol):
    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None = None, reaction: str | None = None,
        timestamp: datetime | None | object = _TIMESTAMP_UNSET, tokens: int | None = None,
    ) -> RowHandle: ...
    def get_message(self, message: RowHandle) -> dict | None: ...
    def get_messages(self, session_id: int, last_n: int | None = None, since: datetime | None = None) -> list[dict]: ...
    def get_turn_history(self, session_id: int, since: datetime | None, token_budget: int | None) -> list[dict]: ...
    def mark_messages_answered(self, messages: list[RowHandle], assistant_message: RowHandle) -> None: ...
    def set_message_reaction(self, message: RowHandle, reaction: str | None) -> dict | None: ...
    def set_message_tokens(self, message: RowHandle, tokens: int, cache_read_tokens: int = 0) -> None: ...
    def has_messages_since(self, session_id: int, since: datetime | None) -> bool: ...
    def has_assistant_message_since(self, session_id: int, since: datetime | None) -> bool: ...
    def record_tool_calls(
        self, session_id: int, tool_calls: list[dict], message_id: RowHandle | None = None,
        timestamp: datetime | None = None,
    ) -> RowHandle: ...
    def link_tool_env_writes_to_message(
        self, session_id: int, message: RowHandle, since: datetime | None = None,
    ) -> None: ...
    def get_tool_calls_by_message(self, session_id: int) -> dict[int, list[dict]]: ...

    def save_transition(
        self, old_state: str | None, action: str | None, new_state: str | None, session_id: int,
        transition_log_level: str, signal_values: dict | None = None, message_id: RowHandle | None = None,
        origin: str | None = None, output_values: dict | None = None,
    ) -> RowHandle: ...
    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> RowHandle: ...
    def link_signal_to_message(self, signal_row: RowHandle, message: RowHandle) -> None: ...
    def get_signals(self, session_id: int) -> list[dict]: ...
    def get_latest_signal_snapshot(self, project_id: str) -> dict | None: ...
    def get_last_transition_timestamp(self, project_id: str, until: datetime | None = None) -> datetime | None: ...
    def get_last_entry_timestamp_for_session(self, session_id: int, state_key: str) -> datetime | None: ...
    def history_cutoff_for_session(self, session_id: int, needs_cutoff: bool) -> datetime | None: ...
    def get_env(self, project_id: str, user: str, until: datetime | None = None) -> dict: ...
    def set_env(self, session_id: int, env: dict, message_id: RowHandle | None = None) -> None: ...
    def get_action_env(self, project_id: str, user: str, until: datetime | None = None) -> dict: ...
    def set_action_env(self, session_id: int, action_env: dict, origin: str | None = None) -> RowHandle: ...
    def get_local_memory(self, session_id: int, until: datetime | None = None) -> dict: ...
    def set_local_memory(self, session_id: int, values: dict) -> None: ...
    def clear_local_memory(self, session_id: int) -> None: ...

    def get_chat_session(self, session_id: int) -> dict | None: ...
    def list_chat_sessions(self, username: str | None, project_id: str, until: datetime | None = None) -> list[dict]: ...
    def get_latest_chat_session(
        self, username: str | None, project_id: str, until: datetime | None = None,
    ) -> dict | None: ...
    def get_user_facts(self, email: str) -> dict[str, Any]: ...
    def get_active_project_id(self, user: str) -> str | None: ...

    def get_archive(self, project_id: str, archive_name: str, revision: int | None = None) -> bytes | None: ...
    def get_archive_content_type(self, project_id: str, archive_name: str, revision: int | None = None) -> str | None: ...
    def get_archives(self, project_id: str, revision: int | None = None) -> dict: ...
    def write_archive_at_revision(
        self, project_id: str, archive_name: str, revision: int, content: bytes, content_type: str,
    ) -> None: ...
    def read_drive_file(self, project_id: str, user_id: str, path: str) -> tuple[bytes, str] | None: ...
    def write_drive_file(
        self, project_id: str, user_id: str, path: str, content: bytes, content_type: str,
        session_id: int | None = None,
    ) -> None: ...
    def list_drive_files(self, project_id: str, user_id: str | None = None, prefix: str = "") -> list[dict]: ...
    def delete_drive_file(self, project_id: str, user_id: str, path: str) -> bool: ...

    def save_translation(self, key: str, src_lang: str, src_text: str, dst_lang: str, dst_text: str) -> None: ...
    def save_system_warning(
        self, username: str, project_id: str, kind: str, message: str, *,
        file: str | None = None, line: int | None = None,
    ) -> int: ...


class TurnTransaction(object):
    def __init__(self, db: Db, session_id: int) -> None:
        self._db = db
        self._session_id = session_id
        self._answering: list[RowHandle] = []

    @property
    def session_id(self) -> int:
        return self._session_id

    def accept(self, text: str) -> RowHandle:
        return self.save_message("user", text, self._session_id)

    def pending_messages(self) -> list[dict]:
        return []

    @asynccontextmanager
    async def exchange(self, answering: list[RowHandle]) -> AsyncIterator["TurnTransaction"]:
        self._answering = list(answering)
        try:
            yield self
        except BaseException:
            self.discard()
            raise
        self.commit()

    def commit(self) -> None:
        self._answering = []

    def discard(self) -> None:
        self._answering = []

    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None = None, reaction: str | None = None,
        timestamp: datetime | None | object = _TIMESTAMP_UNSET, tokens: int | None = None,
    ) -> RowHandle:
        return RowHandle(self._db.save_message(
            role, content, session_id, audio_text=audio_text, reaction=reaction, timestamp=timestamp, tokens=tokens,
        ))

    def get_message(self, message: RowHandle) -> dict | None:
        return self._db.get_message(message.id) if message.id is not None else None

    def get_messages(self, session_id: int, last_n: int | None = None, since: datetime | None = None) -> list[dict]:
        return self._db.get_messages(session_id, last_n=last_n, since=since)

    def get_turn_history(self, session_id: int, since: datetime | None, token_budget: int | None) -> list[dict]:
        history = self._db.get_turn_history(session_id, since, token_budget)
        return history + [dict(OPENING_USER_TURN)] * (not self._answering)

    def mark_messages_answered(self, messages: list[RowHandle], assistant_message: RowHandle) -> None:
        self._db.mark_messages_answered([m.id for m in messages if m.id is not None], _required(assistant_message))

    def set_message_reaction(self, message: RowHandle, reaction: str | None) -> dict | None:
        return self._db.set_message_reaction(_required(message), reaction)

    def set_message_tokens(self, message: RowHandle, tokens: int, cache_read_tokens: int = 0) -> None:
        self._db.set_message_tokens(_required(message), tokens, cache_read_tokens)

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        return self._db.has_messages_since(session_id, since)

    def has_assistant_message_since(self, session_id: int, since: datetime | None) -> bool:
        return self._db.has_assistant_message_since(session_id, since)

    def record_tool_calls(
        self, session_id: int, tool_calls: list[dict], message_id: RowHandle | None = None,
        timestamp: datetime | None = None,
    ) -> RowHandle:
        return RowHandle(self._db.record_tool_calls(session_id, tool_calls, message_id=row_id(message_id), timestamp=timestamp))

    def link_tool_env_writes_to_message(
        self, session_id: int, message: RowHandle, since: datetime | None = None,
    ) -> None:
        self._db.link_tool_env_writes_to_message(session_id, _required(message), since=since)

    def get_tool_calls_by_message(self, session_id: int) -> dict[int, list[dict]]:
        return self._db.get_tool_calls_by_message(session_id)

    def save_transition(
        self, old_state: str | None, action: str | None, new_state: str | None, session_id: int,
        transition_log_level: str, signal_values: dict | None = None, message_id: RowHandle | None = None,
        origin: str | None = None, output_values: dict | None = None,
    ) -> RowHandle:
        return RowHandle(self._db.save_transition(
            old_state, action, new_state, session_id, transition_log_level=transition_log_level,
            signal_values=signal_values, message_id=row_id(message_id), origin=origin, output_values=output_values,
        ))

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> RowHandle:
        return RowHandle(self._db.save_signal_snapshot(values, session_id, row_id(message_id), output_values=output_values))

    def link_signal_to_message(self, signal_row: RowHandle, message: RowHandle) -> None:
        self._db.link_signal_to_message(_required(signal_row), _required(message))

    def get_signals(self, session_id: int) -> list[dict]:
        return self._db.get_signals(session_id)

    def get_latest_signal_snapshot(self, project_id: str) -> dict | None:
        return self._db.get_latest_signal_snapshot(project_id)

    def get_last_transition_timestamp(self, project_id: str, until: datetime | None = None) -> datetime | None:
        return self._db.get_last_transition_timestamp(project_id, until=until)

    def get_last_entry_timestamp_for_session(self, session_id: int, state_key: str) -> datetime | None:
        return self._db.get_last_entry_timestamp_for_session(session_id, state_key)

    def history_cutoff_for_session(self, session_id: int, needs_cutoff: bool) -> datetime | None:
        return self._db.history_cutoff_for_session(session_id, needs_cutoff)

    def get_env(self, project_id: str, user: str, until: datetime | None = None) -> dict:
        return self._db.get_env(project_id, user, until=until)

    def set_env(self, session_id: int, env: dict, message_id: RowHandle | None = None) -> None:
        self._db.set_env(session_id, env, message_id=row_id(message_id))

    def get_action_env(self, project_id: str, user: str, until: datetime | None = None) -> dict:
        return self._db.get_action_env(project_id, user, until=until)

    def set_action_env(self, session_id: int, action_env: dict, origin: str | None = None) -> RowHandle:
        return RowHandle(self._db.set_action_env(session_id, action_env, origin=origin))

    def get_local_memory(self, session_id: int, until: datetime | None = None) -> dict:
        return self._db.get_local_memory(session_id, until=until)

    def set_local_memory(self, session_id: int, values: dict) -> None:
        self._db.set_local_memory(session_id, values)

    def clear_local_memory(self, session_id: int) -> None:
        self._db.clear_local_memory(session_id)

    def get_chat_session(self, session_id: int) -> dict | None:
        return self._db.get_chat_session(session_id)

    def list_chat_sessions(self, username: str | None, project_id: str, until: datetime | None = None) -> list[dict]:
        return self._db.list_chat_sessions(username, project_id, until=until)

    def get_latest_chat_session(
        self, username: str | None, project_id: str, until: datetime | None = None,
    ) -> dict | None:
        return self._db.get_latest_chat_session(username, project_id, until=until)

    def get_user_facts(self, email: str) -> dict[str, Any]:
        return self._db.get_user_facts(email)

    def get_active_project_id(self, user: str) -> str | None:
        return self._db.get_active_project_id(user)

    def get_archive(self, project_id: str, archive_name: str, revision: int | None = None) -> bytes | None:
        return self._db.get_archive(project_id, archive_name, revision)

    def get_archive_content_type(self, project_id: str, archive_name: str, revision: int | None = None) -> str | None:
        return self._db.get_archive_content_type(project_id, archive_name, revision)

    def get_archives(self, project_id: str, revision: int | None = None) -> dict:
        return self._db.get_archives(project_id, revision)

    def write_archive_at_revision(
        self, project_id: str, archive_name: str, revision: int, content: bytes, content_type: str,
    ) -> None:
        self._db.write_archive_at_revision(project_id, archive_name, revision, content, content_type)

    def read_drive_file(self, project_id: str, user_id: str, path: str) -> tuple[bytes, str] | None:
        return self._db.read_drive_file(project_id, user_id, path)

    def write_drive_file(
        self, project_id: str, user_id: str, path: str, content: bytes, content_type: str,
        session_id: int | None = None,
    ) -> None:
        self._db.write_drive_file(project_id, user_id, path, content, content_type, session_id=session_id)

    def list_drive_files(self, project_id: str, user_id: str | None = None, prefix: str = "") -> list[dict]:
        return self._db.list_drive_files(project_id, user_id, prefix)

    def delete_drive_file(self, project_id: str, user_id: str, path: str) -> bool:
        return self._db.delete_drive_file(project_id, user_id, path)

    def save_translation(self, key: str, src_lang: str, src_text: str, dst_lang: str, dst_text: str) -> None:
        self._db.save_translation(key, src_lang, src_text, dst_lang, dst_text)

    def save_system_warning(
        self, username: str, project_id: str, kind: str, message: str, *,
        file: str | None = None, line: int | None = None,
    ) -> int:
        return self._db.save_system_warning(username, project_id, kind, message, file=file, line=line)


def _required(handle: RowHandle) -> int:
    if handle.id is None:
        raise RuntimeError(f"{handle!r} has no row yet: it is committed at the end of the exchange.")
    return handle.id
