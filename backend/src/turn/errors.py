"""TurnServiceError: shared between TurnService and TurnProcessor, both
raising it for the same reasons — lives in its own module rather than
either one importing the other just for this.
"""
from __future__ import annotations

from system.service_error import ServiceError


class TurnServiceError(ServiceError):
    pass
