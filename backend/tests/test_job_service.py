"""JobService's own lifecycle contract: task types register before
start(), never after — the one ordering that makes "a hibernated row
nobody can hydrate" impossible by construction."""
from __future__ import annotations

import pytest

from conftest import make_test_job_service

pytestmark = pytest.mark.contract


def test_a_task_type_cannot_be_registered_after_start(db):
    service = make_test_job_service(db)
    service.register_task_type("early", lambda key, username, payload: None)
    service.start()
    try:
        with pytest.raises(RuntimeError, match="after start"):
            service.register_task_type("late", lambda key, username, payload: None)
    finally:
        service.stop()


def test_a_task_type_is_registered_once(db):
    service = make_test_job_service(db)
    service.register_task_type("t", lambda key, username, payload: None)
    with pytest.raises(ValueError, match="already registered"):
        service.register_task_type("t", lambda key, username, payload: None)


def test_start_is_idempotent(db):
    service = make_test_job_service(db)
    service.start()
    try:
        service.start()
    finally:
        service.stop()
