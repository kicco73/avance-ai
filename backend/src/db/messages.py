from __future__ import annotations

from datetime import datetime
import logging

from .models import Message
from .utils import _utc_iso

logger = logging.getLogger(__name__)

class MessageMixin:

    def save_message(self, role: str, content: str, session_id: int, audio_text: str | None=None) -> int:
        message = Message.create(role=role, content=content, session=session_id, audio_text=audio_text)
        return message.id

    def get_message_audio_text(self, message_id: int) -> str | None:
        message = Message.get_or_none(Message.id == message_id)
        return message.audio_text if message is not None else None

    def delete_message(self, message_id: int) ->  None:
        logging.warning(f"deleting message id {message_id}")
        Message.delete().where(Message.id == message_id).execute()

    def get_message(self, message_id: int) -> dict | None:
        message = Message.get_or_none(Message.id == message_id)
        if message is None:
            return None
        return {'id': message.id, 'role': message.role, 'content': message.content, 'audio_text': message.audio_text, 'timestamp': _utc_iso(message.timestamp), 'session_id': message.session.id}

    def get_messages(self, session_id: int, last_n: int | None=None, since: datetime | None=None) -> list[dict]:
        query = Message.select().where(Message.session == session_id).order_by(Message.timestamp.desc())
        if since is not None:
            query = query.where(Message.timestamp > since)
        if last_n is not None:
            query = query.limit(last_n)
        rows = list(query)
        rows.reverse()
        return [{'id': m.id, 'role': m.role, 'content': m.content, 'audio_text': m.audio_text, 'timestamp': _utc_iso(m.timestamp), 'session_id': session_id} for m in rows]

    def has_messages_since(self, session_id: int, since: datetime | None) -> bool:
        query = Message.select().where(Message.session == session_id)
        if since is not None:
            query = query.where(Message.timestamp > since)
        return query.exists()
