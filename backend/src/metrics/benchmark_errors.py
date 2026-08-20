"""BenchmarkRunService's ServiceError subclass; no handler of its own to
register since error_handlers.py's ServiceError handler already covers it.
"""
from __future__ import annotations

from service_error import ServiceError


class BenchmarkServiceError(ServiceError):
    pass
