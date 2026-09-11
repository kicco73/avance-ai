from __future__ import annotations

import asyncio

import pytest

from system import bus
from system.bus import MAX_CONVERSIONS, Message

pytestmark = pytest.mark.contract

TYPE = "test.thing"


class _Sender:

    def __init__(self) -> None:
        self.bounced_back: list[Message] = []

    async def bounced(self, message: Message) -> None:
        self.bounced_back.append(message)


class _Raising:

    async def bounced(self, message: Message) -> None:
        raise ValueError("nothing here can do that")


@pytest.fixture(autouse=True)
def clean_bus():
    bus._reset_for_tests()
    yield
    bus._reset_for_tests()


def _message(**kwargs) -> Message:
    return Message(type=TYPE, body="hello", username="alice", **kwargs)


def test_a_message_nobody_is_registered_for_comes_back_to_its_sender():
    sender = _Sender()

    asyncio.run(bus.publish_with_bounceback(_message(), sender))

    assert [m.type for m in sender.bounced_back] == [TYPE]
    assert sender.bounced_back[0].body == "hello"


def test_a_delivered_message_never_bounces():
    taken: list[Message] = []

    async def take(message: Message) -> None:
        taken.append(message)

    bus.subscribe(TYPE, take)
    sender = _Sender()

    asyncio.run(bus.publish_with_bounceback(_message(), sender))

    assert len(taken) == 1
    assert sender.bounced_back == []


def test_a_listener_that_raises_still_counts_as_having_taken_it():
    async def take(message: Message) -> None:
        raise RuntimeError("my problem, not the sender's")

    bus.subscribe(TYPE, take)
    sender = _Sender()

    asyncio.run(bus.publish_with_bounceback(_message(), sender))

    assert sender.bounced_back == []


def test_a_message_dropped_for_looping_bounces_too():
    async def take(message: Message) -> None:
        pass

    bus.subscribe(TYPE, take)
    sender = _Sender()

    asyncio.run(bus.publish_with_bounceback(_message(conversions=MAX_CONVERSIONS + 1), sender))

    assert len(sender.bounced_back) == 1


def test_the_sender_decides_what_undeliverable_means():
    with pytest.raises(ValueError, match="nothing here can do that"):
        asyncio.run(bus.publish_with_bounceback(_message(), _Raising()))
