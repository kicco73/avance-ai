"""chat.chart's own wire contract: one `{'line', 'value'}` dict per bar,
written as its own argument, and `max_scale` — the value a full bar
stands for, `None` when the chart is left to scale to its own values.
Observed where the frontend observes it: the output.chart message on the
bus (see backend/src/docs/BUS.md)."""
from __future__ import annotations

import pytest

from conftest import make_test_namespace_factory
from system import bus
from system.bus import OUTPUT_CHART, Message

pytestmark = pytest.mark.contract


class _Watcher:
    def __init__(self) -> None:
        self.seen: list[Message] = []

    async def receive(self, message: Message) -> None:
        self.seen.append(message)


@pytest.fixture
def watcher():
    bus._reset_for_tests()
    watching = _Watcher()
    bus.subscribe(OUTPUT_CHART, watching.receive)
    yield watching
    bus._reset_for_tests()


def _namespace(db):
    return make_test_namespace_factory(db).chat_live(project_id="proj").with_session(7)


def test_a_chart_publishes_one_bar_per_argument_and_the_full_scale_it_was_given(watcher, db):
    _namespace(db).chart(
        "Score",
        {"line": "Emotional exhaustion", "value": 12},
        {"line": "Overall", "value": 42},
        max_scale=100,
    )

    assert [message.body for message in watcher.seen] == [{
        "title": "Score",
        "series": [{"line": "Emotional exhaustion", "value": 12}, {"line": "Overall", "value": 42}],
        "max_scale": 100,
    }]
    assert watcher.seen[0].session_id == 7


def test_a_chart_with_no_full_scale_says_so_rather_than_inventing_one(watcher, db):
    _namespace(db).chart("Score", {"line": "Overall", "value": 42})

    assert watcher.seen[0].body["max_scale"] is None
