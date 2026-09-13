"""The Bus: typed messages, producers, listeners.

Not the event dispatcher in `events/`, and the difference is the reason
both exist. An event there is a *fact that happened* — StateChanged,
AvailabilityChanged — with no destination and no answer. A message here
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

from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Protocol

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

# Inbound: what a person sent, in the form it arrived or was converted to.
INPUT_AUDIO = "input.audio"
INPUT_TEXT = "input.text"

# Outbound: what is being said back, in increasing concreteness — the
# written reply, the text meant to be spoken, the audio itself.
OUTPUT_TEXT = "output.text"
# One piece of a message being written, as it is written. An empty one
# means the writing has started and nothing is readable yet — which is
# what an interface shows as typing dots, and the only honest moment to
# show them: a message accepted is not a reply being composed, and a turn
# can still be refused in between.
OUTPUT_TEXT_STREAM = "output.text_stream"
OUTPUT_SPEECH = "output.speech"
OUTPUT_AUDIO_STREAM = "output.audio_stream"

# One tool call, in both its phases — what the conversation is doing
# while nothing readable is being written.
OUTPUT_TOOL = "output.tool"

# The choices a person is being offered right now. They belong to the
# state the conversation is in, not to whatever produced the last
# message, which is why they travel on their own — and why the scope is
# the state and not the interface showing them.
STATE_BUTTONS = "state.buttons"

# One of those choices, taken. A person acting in a conversation, like
# saying something — and on the same road, so the two cannot overtake
# each other.
INPUT_BUTTON = "input.button"

# The person's own reaction to a message somebody else wrote. The
# model's own reaction to theirs is OUTPUT_REACTION: two facts about two
# different messages, never one field of the other.
INPUT_REACTION = "input.reaction"

# --- One conversation --------------------------------------------------
#
# Requests are verbs and announcements are nouns; the first segment is
# the scope, never the direction. Everything here is addressed by
# session_id except the two that cannot be: entering a conversation and
# creating one name the project instead, because the session is what
# they are asking for.

# I am showing a conversation of this kind for this project: give me the
# active one, or make one if there is none. It also says this connection
# is now watching that session, which is how anything the server decides
# on its own can reach it.
SESSION_ENTER = "session.enter"

# Make a new one regardless, closing whatever was active.
SESSION_CREATE = "session.create"

# I have stopped watching. Not symmetry: without it a connection that
# navigated away keeps being told about a conversation it no longer
# shows, and an operator has no way to say they left. Handled by the
# socket itself and never published: who is watching what is the
# socket's own bookkeeping, and no listener has anything to do about it.
SESSION_EXIT = "session.exit"

# Which conversation this is, and everything that describes it: where it
# stands, what it can reach, whether it speaks. Announced on entering and
# on creating, never asked for on its own.
SESSION_INFO = "session.info"

# Bring back what was said before this point — paging backwards. What is
# on screen when a conversation opens arrives without asking (see
# SESSION_MESSAGES).
SESSION_RECALL = "session.recall"

# What was said, in answer to entering or recalling.
SESSION_MESSAGES = "session.messages"

# Somebody is now in this conversation and has been told what it is. What
# a state has to say before anybody says anything is said in answer to
# this, by whoever runs turns — so entering announces, and opening is a
# reaction to the announcement rather than a kind of request that the
# queue of requests has to recognise. Published after the whole
# announcement, never before: what frames a conversation reaches the
# person ahead of anything the conversation says.
SESSION_OPENED = "session.opened"

# The person closes the conversation.
SESSION_TERMINATE = "session.terminate"

# It has been closed, by whoever decided — often the server itself
# (another channel taking over, a revision that stopped building). Named
# apart from the request on purpose: one letter between a verb and a fact
# is an invitation to get it wrong.
SESSION_ENDED = "session.ended"

# There is no conversation to be had, and why: the project is paused, its
# terms have not been accepted, there is no project at all, or nothing in
# this build answers for chat. A refusal, not a failure — whoever shows a
# chat shows a different screen for each.
SESSION_BLOCKED = "session.blocked"

# Speak, or stop speaking, in this conversation: whether the model is
# asked for the spoken version of its reply. Its own message because it
# changes mid-conversation, and because a turn nobody asked for — an
# opening message — has no request to carry it.
SESSION_SPEAK = "session.speak"

# This conversation has been handed to a person. Named for the session
# because that is what it is about, but delivered to that identity's
# connections rather than to whoever is watching the session: the point
# of it is to reach an operator who is not in the conversation yet.
SESSION_TAKEN_OVER = "session.taken_over"

# Something one identity's interfaces may want to show — a task's own
# snippet, a state that moved while nobody was looking. Not content and
# not a fact about a turn: a nudge, addressed to whoever that person has
# open. Whether anything is listening is not the producer's business.
UI_NOTIFICATION = "ui.notification"

# An administrator-facing warning about the installation itself — a
# published revision that stopped building, and whatever joins it later.
# Its own type because an interface may well show a nudge and not this:
# it is addressed to a role, not to a person doing something.
UI_SYSTEM_WARNING = "ui.system_warning"

# How far along a job is, as the broadcaster batches it. Every JobQueue
# reports through that one broadcaster, so this is not the benchmark's
# alone however much the benchmark screen is what watches it. Published
# rather than pushed so the broadcaster — which is core — holds no
# reference to whatever interface happens to be watching (see
# broadcaster.Broadcaster).
UI_PROGRESS = "ui.progress"

# Somebody reacted to a message — the model to what the person just
# said. A fact about that message, not about the answer to it.
OUTPUT_REACTION = "output.reaction"

# The conversation moved: which state it is in now, and what moved it.
# Published when it changes, because that is when it is news — a reader
# keeps the last one it was told.
STATE_CHANGED = "state.changed"

# What went wrong, when something did: `code` is what happened, and
# whoever is speaking to the person writes the sentence. It ends an
# exchange in place of the answer.
OUTPUT_ERROR = "output.error"

TOOL_SEND_MAIL = "tool.send_mail"

# Named places the core assembles something and anything may add to it.
# Not messages: nothing is delivered and nobody is notified — someone
# asks, synchronously, and whoever registered fills in its part. Mostly
# that someone is the core, because the things a skill has to reach are
# all built before or outside any turn: the boot-time router, a
# request's own response, a read of the configuration. POINT_CORE_
# SERVICES runs the other way and is the reason this says "someone"
# rather than "the core".
POINT_API_STATE = "api.state"
POINT_CONFIG_SERVICES = "config.services"
POINT_HTTP_CONTROLLERS = "http.controllers"

# The composed core, offered to whoever asks for it. A skill starts at
# boot, long before db/TurnService/SchedulerService exist, so it cannot
# be handed them as arguments — which is why every skill's start() was
# growing a parameter for each core object any one of them happened to
# need. Instead the core contributes itself here once it is composed,
# and a skill collects it from inside work that runs later: a
# POINT_HTTP_CONTROLLERS contributor, or the first message it handles.
# Nothing declares a dependency and nothing orders anything — the only
# rule is that a collect must not run before main.py has contributed,
# which is what "later" means here.
POINT_CORE_SERVICES = "core.services"

# Which loader answers "give me this project's automaton". The core
# builds the Db/Archive-backed one and offers the choice here; a package
# that knows better replaces it (see project/archive/loader_choice.py).
# A point rather than a branch in main.py because the alternatives live
# in packages a build may not contain at all: the platform's
# compiled-or-interpreted loader, and a product's single-package one.
POINT_AUTOMATON_LOADER = "automaton.loader"

# A revision has just been published, and whoever can turn one into a
# package may say what it produced. The publisher never asks whether a
# compiler is installed: it collects, and a build without one collects
# nothing — which is the same answer as a compile that failed.
POINT_PROJECT_PUBLISHED = "project.published"

# Whether a turn should also ask the model for a spoken version of its
# reply. Core owns the prompt fragment that asks (tracking/prompt.py's
# AudioPrompt) and cannot own the answer: a spoken reply is worth asking
# for only if something can speak it, and what speaks is a skill. A point
# rather than a flag threaded from main.py because the two halves of the
# old gate — is the service configured, did this project narrow it —
# meant core knowing the name of a package a build may not contain (see
# tracking/spoken_reply.py).
POINT_SPOKEN_REPLY = "turn.spoken_reply"

# What each optional service says it can do for one conversation (see
# tracking/session_services.py). Nobody registered means nothing offers
# anything, which is what a build without those packages should conclude.
POINT_SESSION_SERVICES = "session.services"

# What a client connected over a socket is allowed to put on the Bus.
# The wire uses these very names — a frame is not translated into
# something else on the way in — so without this list the socket would
# be an open injection point: a browser could publish an internal type
# and find listeners for it. A client speaks as a person, and a person
# says things.
CLIENT_INJECTABLE = frozenset({
    INPUT_TEXT, INPUT_BUTTON, INPUT_REACTION,
    SESSION_ENTER, SESSION_CREATE, SESSION_RECALL,
    SESSION_TERMINATE, SESSION_SPEAK,
})

# How deep a chain of conversions may go before something is looping: a
# handler that publishes the type it consumes would otherwise recur
# forever, and the first one to do it will not do it on purpose.
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
    #: The channel's own id for the message a person actually sent —
    #: unchanged across conversions, so a log line ties them together.
    origin_id: str | None = None
    #: The type this message was converted from, if it was: what tells a
    #: consumer that a text arrived as speech (see WhatsApp's own spoken
    #: replies), without the producer having to say so separately.
    converted_from: str | None = None
    #: Media type of `body` where that is not implied by `type`.
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
#: A contribution point's handler: it is handed the thing being
#: assembled and adds to it. Synchronous, because every point is.
Contributor = Callable[[Any], None]

_listeners: dict[str, list[Listener]] = {}
_contributors: dict[str, list[Contributor]] = {}


def subscribe(type: str, listener: Listener) -> None:
    _listeners.setdefault(type, []).append(listener)


def unsubscribe(type: str, listener: Listener) -> None:
    listeners = _listeners.get(type)
    if listeners is not None and listener in listeners:
        listeners.remove(listener)


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
    listeners = handlers_for(message.type)
    # INFO while the Bus is young: every delivery, with what identifies
    # the conversation and how many listeners took it. Drop to DEBUG once
    # the traffic is understood.
    logger.info(
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
    _contributors.setdefault(point, []).append(contributor)


def withdraw(point: str, contributor: Contributor) -> None:
    """The mirror of unsubscribe, for a contributor that only wanted one
    exchange — see docs/BUS.md."""
    contributors = _contributors.get(point)
    if contributors is not None and contributor in contributors:
        contributors.remove(contributor)


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
