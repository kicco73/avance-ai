"""ChatServiceError: shared between ChatService and TurnProcessor, both
raising it for the same reasons — lives in its own module rather than
either one importing the other just for this.
"""
from __future__ import annotations

from service_error import ServiceError


class ChatServiceError(ServiceError):
    pass
