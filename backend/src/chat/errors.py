"""ChatServiceError: shared between ChatService and TurnProcessor (see
turn_processor.py) — both raise it for the same reasons (a session/
message that doesn't belong to the caller, a state that rejects chat, a
turn already in progress), so it lives in its own module rather than
either one importing the other just for this.
"""
from __future__ import annotations

from service_error import ServiceError


class ChatServiceError(ServiceError):
    pass
