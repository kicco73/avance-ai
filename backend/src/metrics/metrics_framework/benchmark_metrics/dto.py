from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class BenchmarkConfiguration(object):
    """Configuration shared by benchmark metrics. max_session_duration_in_minutes
    is the system-level duration reference used to normalize transition latency."""

    max_session_duration_in_minutes: float = 60.0

    def __post_init__(self) -> None:
        if self.max_session_duration_in_minutes <= 0:
            raise ValueError("max_session_duration_in_minutes must be > 0")


@dataclass(frozen=True)
class BenchmarkObservation(object):
    """One atomic comparison between expert expectation and actual behavior."""

    session_id: int
    message_id: int
    expected_state: str | None
    actual_state: str | None
    state_agreement: float | None
    signal_agreements: dict[str, float] = field(default_factory=dict)
    signal_errors: dict[str, float] = field(default_factory=dict)
    signal_signed_errors: dict[str, float] = field(default_factory=dict)
    expected_transition: bool = False
    message_delay: int | None = None
    time_delay_seconds: float | None = None
    transition_responsiveness: float | None = None


@dataclass(frozen=True)
class BenchmarkMetricResult(object):
    """Uniform result returned by every benchmark metric."""

    name: str
    value: float
    mean: float | None = None
    median: float | None = None
    standard_deviation: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    sample_count: int = 0
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    calculated_at: datetime | None = None
