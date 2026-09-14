from __future__ import annotations

import pytest

from system import bus
from system.bus import POINT_TRIGGER_NAMESPACES

from event.event_namespace import EventNamespace


@pytest.fixture(autouse=True)
def event_namespace():
    namespace = EventNamespace()
    declare = lambda namespaces: namespaces.declare(namespace)  # noqa: E731
    bus.contribute(POINT_TRIGGER_NAMESPACES, declare)
    yield namespace
    bus.withdraw(POINT_TRIGGER_NAMESPACES, declare)
