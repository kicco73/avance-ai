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
from system.bus import POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill
from testing import config as testing_config

logger = LoggerFactory.get_logger(__name__)


class TestingSkill(Skill):

    key = "testing"
    ui_label = "Testing"
    ui_description = "Benchmark runs and their aggregated results."

    def __init__(self) -> None:
        self._config = None

    def start_service(self, raw: dict, path: Path) -> None:
        self._config = testing_config.parse(raw, path)

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(testing_config.public_fields(self._config))

    def register_controllers(self, controllers: list) -> None:
        """Built here rather than at start: a run needs the turn engine it
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
            max_concurrent=self._config.max_concurrent_tests,
            broadcaster=broadcaster,
            max_jobs_per_minute=self._config.max_tests_per_minute,
            min_job_interval_ms=self._config.min_test_interval_ms,
        )
        service = TestingService(
            core["db"], core["ai_test_service"], core["tracking_service"], queue,
            core["project_service"], broadcaster,
        )
        controllers.append(construct(TestingController, {**core, "testing_service": service}))
        # Offered to whoever collects the core registry next, the same way
        # main.py offers what it composed: a skill that builds a service
        # others may legitimately need is the only one that can put it
        # there (see bus.POINT_CORE_SERVICES).
        bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({"testing_service": service}))
        logger.info("benchmarking started — up to %d run(s) at a time.", self._config.max_concurrent_tests)
