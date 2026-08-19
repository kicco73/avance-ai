"""BenchmarkServiceError: BenchmarkRunService's own ServiceError subclass
— same shape as chat/errors.py's ChatServiceError, no handler of its own
to register (error_handlers.py's ServiceError handler already covers it).
"""
from __future__ import annotations

from service_error import ServiceError


class BenchmarkServiceError(ServiceError):
    pass
