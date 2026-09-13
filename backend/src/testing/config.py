from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import optional_non_negative_int, optional_positive_int, optional_section

SECTION = "test-service"


@dataclass(frozen=True)
class TestServiceConfig:
    max_concurrent_tests: int
    max_tests_per_minute: int
    min_test_interval_ms: int


def parse(raw: dict, path: Path) -> TestServiceConfig:
    """Always a config: the section is optional and every field has a
    default, so a deployment that never mentions benchmarking still gets
    the pool the runs would use if it started one."""
    section = optional_section(raw, SECTION, path)
    return TestServiceConfig(
        max_concurrent_tests=optional_positive_int(section, SECTION, "max-concurrent-tests", path, 4),
        max_tests_per_minute=optional_positive_int(section, SECTION, "max-tests-per-minute", path, 1_000_000),
        min_test_interval_ms=optional_non_negative_int(section, SECTION, "min-test-interval-ms", path, 0),
    )


def public_fields(config: TestServiceConfig) -> dict:
    return {
        "max-concurrent-tests": config.max_concurrent_tests,
        "max-tests-per-minute": config.max_tests_per_minute,
        "min-test-interval-ms": config.min_test_interval_ms,
    }
