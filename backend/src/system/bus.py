"""The Bus: typed messages, producers, listeners.

Not the event dispatcher in `events/`, and the difference is the reason
both exist. An event there is a *fact that happened* — AvailabilityChanged,
ProjectRevisionBuildFailed — with no destination and no answer. A message here
is a *delivery*: it has a type, it carries everything its handler needs,
and something is expected to happen to it.

Two operations, and the split between them is the whole design:

`handlers_for(type)` is synchronous and answers immediately. It is what a
producer asks *before* producing, so that "nobody can do this" is a
normal answer given in the same breath rather than a message vanishing
into a system that will never reply. WhatsApp asks whether anything
decodes audio and, told no, says so to the user on the spot.

`publish(message)` is asynchronous, and awaits each listener in turn.
Every producer already runs outside a request — the WhatsApp webhook
answers 200 and hands the work to a background task — so there is
nothing to protect from a listener that takes two seconds, and ordering
is worth more than concurrency here.

A converted message keeps its origin: Listen does not publish "a text",
it publishes *this* message with a text body and `converted_from` set. A
handler that loses the envelope loses which conversation the answer
belongs to, which is why the envelope is the message and the body is
just one of its fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Protocol

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

INPUT_AUDIO = "input.audio"
INPUT_TEXT = "input.text"
OUTPUT_TEXT = "output.text"
OUTPUT_TEXT_STREAM = "output.text_stream"
OUTPUT_SPEECH = "output.speech"
OUTPUT_AUDIO_STREAM = "output.audio_stream"
OUTPUT_TOOL = "output.tool"
STATE_BUTTONS = "state.buttons"
INPUT_BUTTON = "input.button"
INPUT_REACTION = "input.reaction"
SESSION_ENTER = "session.enter"

SESSION_CREATE = "session.create"
SESSION_EXIT = "session.exit"
SESSION_INFO = "session.info"
SESSION_RECALL = "session.recall"

SESSION_MESSAGES = "session.messages"
SESSION_OPENED = "session.opened"

SESSION_TERMINATE = "session.terminate"
SESSION_ENDED = "session.ended"
SESSION_BLOCKED = "session.blocked"
SESSION_SPEAK = "session.speak"
SESSION_TAKEN_OVER = "session.taken_over"
UI_NOTIFICATION = "ui.notification"
UI_PROGRESS = "ui.progress"
OUTPUT_REACTION = "output.reaction"
STATE_CHANGED = "state.changed"
ENV_CHANGED = "env.changed"
OUTPUT_ERROR = "output.error"

TASK_STARTED = "task.started"
TASK_ENDED = "task.ended"
TOOL_SEND_MAIL = "tool.send_mail"
POINT_API_STATE = "api.state"
POINT_CONFIG_SERVICES = "config.services"
POINT_HTTP_CONTROLLERS = "http.controllers"
POINT_CORE_SERVICES = "core.services"
POINT_AUTOMATON_LOADER = "automaton.loader"
POINT_PROJECT_PUBLISHED = "project.published"
POINT_SPOKEN_REPLY = "turn.spoken_reply"
POINT_SESSION_SERVICES = "session.services"
CLIENT_INJECTABLE = frozenset({
    INPUT_TEXT, INPUT_BUTTON, INPUT_REACTION,
    SESSION_ENTER, SESSION_CREATE, SESSION_RECALL,
    SESSION_TERMINATE, SESSION_SPEAK,
})
MAX_CONVERSIONS = 4


@dataclass(frozen=True, slots=True)
class Message:
    """One delivery. `type` is what listeners register for; everything
    else travels with it so a conversion never has to reconstruct where
    the message came from."""

    type: str
    body: Any
    username: str
    project_id: str | None = None
    session_id: int | None = None
    channel: str | None = None
    origin_id: str | None = None
    converted_from: str | None = None
    mime: str | None = None
    conversions: int = 0

    def converted(self, type: str, body: Any, mime: str | None = None) -> "Message":
        """This same message, carrying a different body. Everything that
        says *which conversation this is* is preserved by construction."""
        return replace(
            self, type=type, body=body, mime=mime,
            converted_from=self.type, conversions=self.conversions + 1,
        )


Listener = Callable[[Message], Awaitable[None]]
Contributor = Callable[[Any], None]

_listeners: dict[str, tuple[Listener, ...]] = {}
_contributors: dict[str, tuple[Contributor, ...]] = {}


def subscribe(type: str, listener: Listener) -> None:
    _listeners[type] = _listeners.get(type, ()) + (listener,)


def unsubscribe(type: str, listener: Listener) -> None:
    remaining = list(_listeners.get(type, ()))
    try:
        remaining.remove(listener)
    except ValueError:
        return
    _listeners[type] = tuple(remaining)


def handlers_for(type: str) -> list[Listener]:
    """Who would handle a message of this type, right now. The one
    synchronous question the Bus answers, and the reason a producer never
    has to publish hopefully."""
    return list(_listeners.get(type, ()))


async def publish(message: Message) -> bool:
    """Hands `message` to every listener registered for its type, in
    subscription order, awaiting each. A listener that raises is logged
    and the rest still run: one broken consumer must not silence the
    others.

    Returns whether anything took it. False means nobody is listening
    for this type — the producer's answer to "can this be done here at
    all", given back by the posting itself rather than by asking first."""
    if message.conversions > MAX_CONVERSIONS:
        logger.error(
            "Dropping %s after %d conversions — a handler is publishing what it consumes.",
            message.type, message.conversions,
        )
        return False
    listeners = _listeners.get(message.type, ())
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "bus %s -> %d listener(s) | user=%s session=%s channel=%s origin=%s from=%s%s",
            message.type, len(listeners), message.username, message.session_id, message.channel,
            message.origin_id, message.converted_from, _body_summary(message),
        )
    for listener in listeners:
        try:
            await listener(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Bus listener for %s failed: %s", message.type, exc)
    return bool(listeners)


class Sender(Protocol):
    """Whoever posts with publish_with_bounceback. A message no listener
    was registered for comes back here, which is how a producer learns
    that nothing in this build can do the thing — by delivery, in the
    same shape everything else arrives in, and not by asking first."""

    async def bounced(self, message: Message) -> None:
        ...


async def publish_with_bounceback(message: Message, sender: Sender) -> None:
    """Posts `message`, and hands it back to `sender.bounced` when no
    listener is registered for its type (or when it was dropped for
    looping past MAX_CONVERSIONS). Nothing is asked of the registry
    beforehand and nothing is returned to branch on: "undeliverable" is
    itself a delivery, so a producer writes what to do about it once, in
    a method with a name, instead of at every call site.

    A listener never declines a message: subscribing to a type already
    declares what it wants. The Bus guarantees routing, not semantics —
    a listener registered for a type that produces nothing useful is a
    bug in that listener, not a state this can report."""
    if not await publish(message):
        await sender.bounced(message)


def _body_summary(message: Message) -> str:
    """Enough of the body to recognise the message in a log, never enough
    to dump a voice note into it."""
    body = message.body
    if isinstance(body, str):
        return f" body={body[:80]!r}"
    if isinstance(body, (bytes, bytearray)):
        return f" body={len(body)} bytes"
    if callable(body):
        return " body=<deferred>"
    return f" body=<{type(body).__name__}>"


def contribute(point: str, contributor: Contributor) -> None:
    """Register to add something to whatever `point` assembles."""
    _contributors[point] = _contributors.get(point, ()) + (contributor,)


def withdraw(point: str, contributor: Contributor) -> None:
    """The mirror of unsubscribe, for a contributor that only wanted one
    exchange — see docs/BUS.md."""
    remaining = list(_contributors.get(point, ()))
    try:
        remaining.remove(contributor)
    except ValueError:
        return
    _contributors[point] = tuple(remaining)


def collect(point: str, target: Any) -> Any:
    """Hands `target` to everyone registered for `point`, in registration
    order, and gives it back. Synchronous and immediate: a contribution
    point is the core asking, not the core announcing — and every one of
    them is a response being built while a caller waits for it.

    Returns `target` so a call site can say what it means in one line:
    `return bus.collect(POINT_API_STATE, payload)`.

    A contributor that raises is not survivable and is never swallowed.
    What a contribution point assembles is the thing the caller is about
    to act on: the loader a build will read every automaton through, the
    router it will answer requests with, the services it will wire
    against. A skill that fails to add its part and is logged past leaves
    a system that runs, answers, and is wrong — a product whose packaged
    loader raised here used to fall back to the database loader without
    a word. The exception travels as the skill raised it; the log line
    below only says which point and which contributor, because the
    traceback alone points at a lambda in a skill file."""
    for contributor in _contributors.get(point, ()):
        try:
            contributor(target)
        except Exception:
            logger.exception(
                "Contributor %s to %s failed.", getattr(contributor, "__qualname__", contributor), point,
            )
            raise
    return target


def _reset_for_tests() -> None:
    """Test-only — the registries are process-globals, like events'."""
    _listeners.clear()
    _contributors.clear()
