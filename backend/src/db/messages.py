from __future__ import annotations

from datetime import datetime
from typing import Any

from peewee import fn

from logging_factory import LoggerFactory

from .models import Message
from .utils import _utc_iso

logger = LoggerFactory.get_logger(__name__)

# Distinguishes "caller didn't pass timestamp" (default=datetime.utcnow
# applies) from "caller explicitly wants NULL" — None can't be the
# sentinel since it's the explicit value being distinguished for.
_TIMESTAMP_UNSET = object()

class MessageMixin:

    def save_message(
        self, role: str, content: str, session_id: int, audio_text: str | None=None, reaction: str | None=None,
        timestamp: datetime | None | object=_TIMESTAMP_UNSET, tokens: int | None=None,
    ) -> int:
        fields: dict[str, Any] = {"role": role, "content": content, "session": session_id, "audio_text": audio_text, "reaction": reaction, "tokens": tokens}
        if timestamp is not _TIMESTAMP_UNSET:
            fields["timestamp"] = timestamp
        message = Message.create(**fields)
        return message.id

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def set_message_reaction(self, message_id: int, reaction: str | None) -> dict | None:
        Message.update(reaction=reaction).where(Message.id == message_id).execute()
        return self.get_message(message_id)

    def set_message_tokens(self, message_id: int, tokens: int, cache_read_tokens: int = 0) -> None:
        Message.update(tokens=tokens, cache_read_tokens=cache_read_tokens).where(Message.id == message_id).execute()

    def delete_message(self, message_id: int) ->  None:
        logger.warning(f"deleting message id {message_id}")
        Message.delete().where(Message.id == message_id).execute()

    def get_message(self, message_id: int) -> dict | None:
        message = Message.get_or_none(Message.id == message_id)
        if message is None:
            return None
        return {
            'id': message.id, 'role': message.role, 'content': message.content, 'audio_text': message.audio_text,
            'reaction': message.reaction, 'tokens': message.tokens, 'cache_read_tokens': message.cache_read_tokens,
            'timestamp': _utc_iso(message.timestamp), 'session_id': message.session.id,
        }

    def get_messages(self, session_id: int, last_n: int | None=None, since: datetime | None=None) -> list[dict]:
        # id, not timestamp: always present (never null, unlike an
        # imported message's) and already the correct order for any
        # session, native or imported.
        query = Message.select().where(Message.session == session_id).order_by(Message.id.desc())
        if since is not None:
            query = query.where(Message.timestamp > since)
        if last_n is not None:
            query = query.limit(last_n)
        rows = list(query)
        rows.reverse()
        return [
            {
                'id': m.id, 'role': m.role, 'content': m.content, 'audio_text': m.audio_text, 'reaction': m.reaction,
                'tokens': m.tokens, 'cache_read_tokens': m.cache_read_tokens, 'timestamp': _utc_iso(m.timestamp),
                'session_id': session_id,
            }
            for m in rows
        ]

    def get_turn_history(self, session_id: int, since: datetime | None, token_budget: int | None) -> list[dict]:
        # FIXME: COALESCE is load-bearing — SUM() over an all-NULL window
        # is NULL, never <= token_budget, which would empty the result.
        if token_budget is None:
            return self.get_messages(session_id, since=since)

        windowed = Message.select(
            Message.id,
            fn.SUM(fn.COALESCE(Message.tokens, 0)).over(order_by=[Message.id.desc()]).alias('running_tokens'),
        ).where(Message.session == session_id)
        if since is not None:
            windowed = windowed.where(Message.timestamp > since)

        cte = windowed.cte('windowed', columns=('id', 'running_tokens'))
        query = (Message
                 .select()
                 .join(cte, on=(Message.id == cte.c.id))
                 .where(cte.c.running_tokens <= token_budget)
                 .order_by(Message.id)
                 .with_cte(cte))
        return [{'id': m.id, 'role': m.role, 'content': m.content, 'audio_text': m.audio_text, 'reaction': m.reaction, 'tokens': m.tokens, 'timestamp': _utc_iso(m.timestamp), 'session_id': session_id} for m in query]

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        query = Message.select().where(Message.session == session_id)
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()

    def has_assistant_message_since(self, session_id: int, since: datetime | None) -> bool:
        """Same shape as has_messages_since, narrowed to the assistant's
        own replies — TrackingProcessor.force_required_tools_for's own
        "has a turn already answered since entering this state" check."""
        query = Message.select().where((Message.session == session_id) & (Message.role == "assistant"))
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()
