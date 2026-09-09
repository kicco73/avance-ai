from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import ConfigError

SECTION = "test-service"


@dataclass(frozen=True)
class TestServiceConfig:
    max_concurrent_tests: int
    max_tests_per_minute: int
    min_test_interval_ms: int


def _positive_int(section: dict, field: str, path: Path, default: int) -> int:
    value = section.get(field, default)
    for bad in filter(lambda v: isinstance(v, bool) or not isinstance(v, int) or v <= 0, [value]):
        raise ConfigError(f"{path}: '{SECTION}.{field}' must be a positive integer if present.")
    return value


def _non_negative_int(section: dict, field: str, path: Path, default: int) -> int:
    value = section.get(field, default)
    for bad in filter(lambda v: isinstance(v, bool) or not isinstance(v, int) or v < 0, [value]):
        raise ConfigError(f"{path}: '{SECTION}.{field}' must be a non-negative integer if present.")
    return value


def parse(raw: dict, path: Path) -> TestServiceConfig:
    """Always a config: the section is optional and every field has a
    default, so a deployment that never mentions benchmarking still gets
    the pool the runs would use if it started one."""
    section = raw.get(SECTION, {})
    for bad in filter(lambda s: not isinstance(s, dict), [section]):
        raise ConfigError(f"{path}: '{SECTION}' section is not a mapping.")
    return TestServiceConfig(
        max_concurrent_tests=_positive_int(section, "max-concurrent-tests", path, 4),
        max_tests_per_minute=_positive_int(section, "max-tests-per-minute", path, 1_000_000),
        min_test_interval_ms=_non_negative_int(section, "min-test-interval-ms", path, 0),
    )


def public_fields(config: TestServiceConfig) -> dict:
    return {
        "max-concurrent-tests": config.max_concurrent_tests,
        "max-tests-per-minute": config.max_tests_per_minute,
        "min-test-interval-ms": config.min_test_interval_ms,
    }
