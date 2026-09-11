"""Benchmarking, as something the platform finds rather than builds.

Nothing outside this package names it: not main.py, not config.py, not
the composition root. A build that leaves `backend/src/testing/` out is
a system that runs conversations and cannot measure them — no /tests
route, no replay pool, no aggregation — with nothing left in the code
saying any of it ever existed.

There is no disabled shape here, unlike a channel: benchmarking is on
when its package is present. `test-service` only sizes the pool the
runs share, and every field of it has a default (see testing/config.py).
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.wiring import construct
from system.bus import POINT_CONFIG_SERVICES, POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.config_services import ui_section
from system.logging_factory import LoggerFactory
from testing import config as testing_config

logger = LoggerFactory.get_logger(__name__)

KEY = "testing"
UI_LABEL = "Testing"
UI_DESCRIPTION = "Benchmark runs and their aggregated results."

_config = None


def start(raw: dict, path: Path) -> None:
    global _config
    _config = testing_config.parse(raw, path)
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update(
        {KEY: ui_section(UI_LABEL, UI_DESCRIPTION, testing_config.public_fields(_config))}
    ))
    bus.contribute(POINT_HTTP_CONTROLLERS, _install)


def _install(controllers: list) -> None:
    """Built here rather than at start(): a run needs the turn engine it
    replays against, and at boot that does not exist yet (see
    bus.POINT_CORE_SERVICES)."""
    from jobs.throttled_job_queue import ThrottledJobQueue
    from testing.testing_service import TestingService
    from testing.testing_controller import TestingController

    core = bus.collect(POINT_CORE_SERVICES, {})
    broadcaster = core["progress_broadcaster"]
    # Its own pool, never the platform SchedulerService's: a benchmark
    # replaying hundreds of sessions must not starve live turns.
    queue = ThrottledJobQueue(
        max_concurrent=_config.max_concurrent_tests,
        broadcaster=broadcaster,
        max_jobs_per_minute=_config.max_tests_per_minute,
        min_job_interval_ms=_config.min_test_interval_ms,
    )
    service = TestingService(
        core["db"], core["ai_test_service"], core["tracking_service"], queue,
        core["project_service"], broadcaster,
    )
    controllers.append(construct(TestingController, {**core, "testing_service": service}))
    logger.info("benchmarking started — up to %d run(s) at a time.", _config.max_concurrent_tests)


def stop() -> None:
    pass
