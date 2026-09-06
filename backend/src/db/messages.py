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

def _turn_key(row: dict) -> tuple:
    """Where a message sits in the conversation *by turn*, not by id. A
    user message belongs to the turn that answered it (`answered_by`); an
    assistant message is that turn. Stored ids alone no longer say this:
    a fragment that arrives while the previous turn is generating is
    written before that turn's own reply, so ordering by id would show it
    inside a turn it had nothing to do with. Fragments still waiting for a
    turn sort last, where they belong."""
    if row["role"] == "user":
        answered_by = row.get("answered_by")
        return (answered_by if answered_by is not None else float("inf"), 0, row["id"])
    return (row["id"], 1, row["id"])


def _group_user_fragments(rows: list[dict]) -> list[dict]:
    """The conversation in turn order, with each turn's own user fragments
    collapsed into one entry carrying the list of their texts. The entry
    keeps the LAST fragment's own id and timestamp: that fragment is the
    one that closes the turn, and everything a turn binds to its user
    message (its Tracking row, the bot's reaction, the input tokens) binds
    to it."""
    grouped: list[dict] = []
    for row in sorted(rows, key=_turn_key):
        previous = grouped[-1] if grouped else None
        same_turn = (
            previous is not None
            and previous["role"] == "user"
            and row["role"] == "user"
            and previous.get("answered_by") == row.get("answered_by")
        )
        if same_turn:
            texts = previous["content"] if isinstance(previous["content"], list) else [previous["content"]]
            grouped[-1] = {**row, "content": [*texts, row["content"]]}
            continue
        grouped.append(dict(row))
    return grouped


def _history_row(m, session_id: int) -> dict:
    return {
        'id': m.id, 'role': m.role, 'content': m.content, 'audio_text': m.audio_text, 'reaction': m.reaction,
        'tokens': m.tokens, 'answered_by': m.answered_by, 'timestamp': _utc_iso(m.timestamp),
        'session_id': session_id,
    }


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
        """The conversation as the model sees it: in turn order, with each
        turn's own user fragments as ONE entry whose `content` is the list
        of their texts, so they arrive as a single user message of several
        blocks rather than as separate turns (see PROJECT_SPECS.md's own
        turn section). A lone fragment keeps `content` a plain string,
        exactly as before, so nothing changes for existing sessions. The
        token budget still cuts message by message, oldest first; a group
        it would cut in half is dropped whole instead."""
        rows = self._turn_history_rows(session_id, since, token_budget)
        if token_budget is not None:
            rows = self._without_half_cut_group(session_id, since, rows)
        return _group_user_fragments(rows)

    def _without_half_cut_group(self, session_id: int, since: datetime | None, rows: list[dict]) -> list[dict]:
        """The budget cuts from the oldest end, so the only group it can
        ever cut in half is the leading one — dropped whole rather than
        handed to the model as a turn missing its own opening."""
        if not rows or rows[0]["role"] != "user":
            return rows
        first = rows[0]
        query = Message.select().where(
            (Message.session == session_id) & (Message.role == "user") & (Message.id < first["id"])
        )
        query = (
            query.where(Message.answered_by == first["answered_by"])
            if first["answered_by"] is not None
            else query.where(Message.answered_by.is_null(True))
        )
        if since is not None:
            query = query.where(Message.timestamp > since)
        if not query.exists():
            return rows
        return [row for row in rows if not (row["role"] == "user" and row["answered_by"] == first["answered_by"])]

    def _turn_history_rows(self, session_id: int, since: datetime | None, token_budget: int | None) -> list[dict]:
        # FIXME: COALESCE is load-bearing — SUM() over an all-NULL window
        # is NULL, never <= token_budget, which would empty the result.
        if token_budget is None:
            query = Message.select().where(Message.session == session_id)
            if since is not None:
                query = query.where(Message.timestamp > since)
            return [_history_row(m, session_id) for m in query.order_by(Message.id)]

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
        return [_history_row(m, session_id) for m in query]

    def unconsumed_user_fragments(self, session_id: int) -> list[dict]:
        """Every user message no turn has answered yet — the fragments
        this session has accumulated. The queue of a coalesced turn is
        exactly this, read off the database rather than held in memory, so
        a restart loses nothing; and unlike "everything after the last
        reply", it stays right for a message that arrived while that reply
        was still being generated."""
        query = (Message
                 .select()
                 .where(
                     (Message.session == session_id)
                     & (Message.role == "user")
                     & (Message.answered_by.is_null(True))
                 ))
        return [
            {'id': m.id, 'role': m.role, 'content': m.content, 'timestamp': _utc_iso(m.timestamp)}
            for m in query.order_by(Message.id)
        ]

    def mark_messages_answered(self, message_ids: list[int], assistant_message_id: int) -> None:
        """Closes the turn over its own fragments: every one of them now
        points at the reply that answered it, so no later turn picks it up
        again (see unconsumed_user_fragments)."""
        if not message_ids:
            return
        (Message
         .update(answered_by=assistant_message_id)
         .where(Message.id.in_(list(message_ids)))
         .execute())

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
