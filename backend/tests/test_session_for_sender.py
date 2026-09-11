"""WebSession().for_sender — the context a message was sent in, re-entered
by whoever handles it.

A Bus listener inherits the publisher's context today, because
bus.publish awaits each listener in the publisher's own task. That is an
accident of how the two channels publish, and it is already false for
system/broadcaster.py (another thread, another loop) and
tracking/wakeup_service.py (a scheduled job). A listener that establishes
its context from the Message works from any producer.
"""
from __future__ import annotations

import contextvars

import pytest

from system.web_session import WebSession

pytestmark = pytest.mark.contract


def _fresh(call):
    return contextvars.Context().run(call)


def test_it_sets_the_identity_a_listener_needs_from_nothing_at_all():
    def body():
        with WebSession().for_sender("alice@example.com", role="user", channel="whatsapp"):
            return WebSession().user, WebSession().role, WebSession().channel

    assert _fresh(body) == ("alice@example.com", "user", "whatsapp")


def test_a_message_with_no_channel_leaves_the_channel_undeclared():
    """Not None: undeclared. A live session's write admission compares
    against the caller's channel, and answering None there would fail the
    comparison quietly instead of saying nobody named one."""
    def body():
        with WebSession().for_sender("alice@example.com", role="user"):
            try:
                return WebSession().channel
            except RuntimeError as exc:
                return str(exc)

    assert "outside a request context" in _fresh(body)


def test_everything_it_set_is_put_back_on_the_way_out():
    """A listener runs inside whatever was already there — a request, or
    another listener. It borrows the context, it does not take it."""
    WebSession().user = "owner@example.com"
    WebSession().role = "supervisor"
    WebSession().channel = "webchat"

    with WebSession().for_sender("alice@example.com", role="user", channel="whatsapp"):
        pass

    assert (WebSession().user, WebSession().role, WebSession().channel) == (
        "owner@example.com", "supervisor", "webchat",
    )


def test_it_puts_things_back_even_when_the_handler_raises():
    WebSession().user = "owner@example.com"
    WebSession().role = "supervisor"

    with pytest.raises(ValueError):
        with WebSession().for_sender("alice@example.com", role="user"):
            raise ValueError("the handler blew up")

    assert (WebSession().user, WebSession().role) == ("owner@example.com", "supervisor")


def test_a_channel_left_undeclared_does_not_clobber_one_already_set():
    """The reset is per-variable: a message with no channel must not put
    back a channel it never set."""
    WebSession().user = "owner@example.com"
    WebSession().role = "supervisor"
    WebSession().channel = "webchat"

    with WebSession().for_sender("alice@example.com", role="user"):
        assert WebSession().channel == "webchat"

    assert WebSession().channel == "webchat"
