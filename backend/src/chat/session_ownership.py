from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from auth.roles import role_satisfies
from chat.errors import ChatServiceError
from db import Db
from session import Session


class SessionOwnership:
    def __init__(self, db: Db) -> None:
        self._db = db

    @staticmethod
    def owns_session(session_username: str) -> bool:
        if session_username == Session().user:
            return True
        return role_satisfies(Session().role, 'supervisor')

    def require_session(self, session_id: int) -> dict:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
        return session

    def require_own_session(self, session_id: int) -> dict:
        session = self._db.get_chat_session(session_id)
        if session is None or not self.owns_session(session["username"]):
            raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
        return session

    def require_own_message(self, message_id: int) -> dict:
        message = self._db.get_message(message_id)
        if message is not None:
            session = self._db.get_chat_session(message["session_id"])
            if session is not None and self.owns_session(session["username"]):
                return message
        raise ChatServiceError("Message not found.", status_code=HTTPStatus.NOT_FOUND)

    def until_from_message(self, message_id: int | None) -> datetime | None:
        if message_id is None:
            return None
        message = self.require_own_message(message_id)
        return datetime.fromisoformat(message["timestamp"]).replace(tzinfo=None)
