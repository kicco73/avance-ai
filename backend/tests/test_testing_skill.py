"""test-service, parsed by the package that owns it.

These moved here from test_config.py when the section left AppConfig:
the core no longer knows benchmarking exists, so what validates its pool
sizing is testing/config.py.
"""
from __future__ import annotations

import pytest

from config import ConfigError
from testing import config as testing_config

pytestmark = pytest.mark.contract


def test_an_absent_section_still_yields_the_defaults_the_pool_would_use():
    config = testing_config.parse({}, "cfg")
    assert config.max_concurrent_tests == 4
    assert config.max_tests_per_minute == 1_000_000
    assert config.min_test_interval_ms == 0


def test_reads_every_configured_value():
    config = testing_config.parse({"test-service": {
        "max-concurrent-tests": 5, "max-tests-per-minute": 30, "min-test-interval-ms": 500,
    }}, "cfg")
    assert (config.max_concurrent_tests, config.max_tests_per_minute, config.min_test_interval_ms) == (5, 30, 500)


def test_an_explicit_zero_interval_is_a_value_not_an_omission():
    assert testing_config.parse({"test-service": {"min-test-interval-ms": 0}}, "cfg").min_test_interval_ms == 0


@pytest.mark.parametrize(("raw", "match"), [
    ({"test-service": {"max-concurrent-tests": 0}}, "max-concurrent-tests"),
    ({"test-service": {"max-tests-per-minute": 0}}, "max-tests-per-minute"),
    ({"test-service": {"min-test-interval-ms": -1}}, "min-test-interval-ms"),
    ({"test-service": {"max-concurrent-tests": True}}, "max-concurrent-tests"),
    ({"test-service": "nope"}, "test-service"),
])
def test_rejects_what_it_cannot_use(raw, match):
    with pytest.raises(ConfigError, match=match):
        testing_config.parse(raw, "cfg")
