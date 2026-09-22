"""chat.write(body_md) hands text to whatever the namespace is bound to
answer through — nothing more. Unbound, it is a no-op, like chat.chart
outside a session; bound, the text reaches the sink in call order. It
has no real-world side effect, so Live and Fake behave the same."""
from __future__ import annotations

import pytest

from automaton.identifier_registry import IdentifierRegistry
from tracking.actuators import FakeChatNamespace, LiveChatNamespace

pytestmark = pytest.mark.contract


class _Sink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def write(self, text: str) -> None:
        self.texts.append(text)


@pytest.mark.parametrize("namespace_class", [LiveChatNamespace, FakeChatNamespace])
def test_write_reaches_the_bound_sink_in_order(namespace_class):
    sink = _Sink()
    chat = namespace_class(project_id="p").with_session(7).with_reply(sink)

    chat.write("Step 1")
    chat.write("**bold**")

    assert sink.texts == ["Step 1", "**bold**"]


def test_write_on_an_unbound_namespace_does_nothing():
    LiveChatNamespace(project_id="p").with_session(7).write("lost")


def test_binding_a_reply_never_mutates_the_shared_namespace():
    shared = LiveChatNamespace(project_id="p")
    sink = _Sink()

    shared.with_reply(sink).write("bound")
    shared.write("unbound")

    assert sink.texts == ["bound"]


def test_write_is_an_on_exit_identifier_and_nothing_else():
    registry = IdentifierRegistry.build([], [])

    assert "write" in IdentifierRegistry.for_on_exit(registry)["chat"]
    assert "chat" not in IdentifierRegistry.for_triggers(registry)
    assert "chat" not in IdentifierRegistry.for_task(registry)
