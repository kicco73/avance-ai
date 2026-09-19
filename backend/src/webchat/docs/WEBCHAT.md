# The browser chat

Conversations held in a browser: the chat window, its turn-by-turn
streaming, and the handover to a human operator. Like every channel it
converts what a person did into a Bus message and what comes back into
something on screen. It never runs a turn itself and never resolves a
session by hand.

It has **no HTTP surface**. `webchat_controller.py` is gone, and with it
`sessions/current`, `POST sessions`, `messages` and `operator-state`:
everything about the conversation you are in is a message on
`/api/core/bus`, and `docs/BUS.md` is the vocabulary.

## What it forwards

`WebchatService.register()` subscribes to what a turn produces —
`TURN_FORWARDED` — and sends each frame to the connection that is
watching:

```text
output.text_stream  output.text     output.speech   output.tool
output.reaction     output.error    state.changed   state.buttons
session.info        session.messages session.blocked session.ended
```

That list is **not** the mirror image of `CLIENT_INJECTABLE`: that is
what a browser may put *on* the Bus, and this is what comes back. A
screen is sent every frame as it happens, which is what makes streaming
possible and is the one thing a channel without a screen has no use for.

It also contributes `want()` at `bus.POINT_SPOKEN_REPLY` — whoever runs
the interface is who says a spoken reply is wanted — and answers
`session.opened`.

## Opening a conversation

The core announces a conversation and publishes `session.opened` when the
transcript it announced was empty. Whether the conversation then *speaks*
is a decision this package makes, not the core's: a browser is greeted on
entering, so `_opened` asks for the message
(`TurnService.open_conversation`) without working out for itself whether
the conversation has already spoken — the event only fires when it has
not.

It answers **only for its own channel**. A session opened elsewhere is
announced on the same Bus, and answering it would be one package greeting
a conversation it does not serve, so `_opened` filters on
`message.channel` before opening anything.

## Frames that arrive before the session does

A session's `init-action` runs at its creation, so its `chat.*` frames are
published before `session.info` (`docs/BUS.md`, "What creating a session
starts"). Between asking and being answered, this package's store does
not yet know which session it is in, so it cannot match those frames on
the session id.

`chatStoreFactory.isAboutOurConversation` is that match: the session id
when it has one, and, while it is still waiting, the session type and the
project — which is what `session.info` and `session.blocked` were already
matched on. `ui.notification`, `output.chart` and `output.progress` go
through it too; without them an `init-action`'s notification was dropped
in silence, as if it were for somebody else's conversation.

The type is half the match because the editor holds a live store, a test
store and a preview store open on the same project at once. They all
re-enter together when the socket reconnects, so on the project alone
each would answer the others' `session.info`, overwrite its own session
id, and then discard the `state.buttons` meant for it — buttons gone,
and nothing to bring them back. A frame carrying no type is matched on
the project, which is all an `init-action`'s own frames carry.

The window that remains is the one `session.info` already lived in: two
stores of the *same* kind waiting on the same project in the same instant
could still take each other's frame. It lasts one round trip.

`ui.notification` keeps a second way in, for a frame carrying no session
at all — what `task.defer` schedules (`ActionTask.later`) — matched on
the project alone.

## Standing down

A live session belongs to one channel at a time, and the rule both sides
of that contention read is `writable = current AND channel is mine`
(`docs/BUS.md`, "Whose conversation it is"). This package is the half
that **stands down**, and the reason is its own entry pattern:
`session.enter` fires on every reload and every reconnection of the
socket. Claiming there would steal the conversation back each time a
forgotten tab woke up, and the two ends would rally the session between
them with nobody having written anything.

So it narrows `current` to `false` on the way out for a conversation that
is not its own, and the browser shows it read-only with a way back
("Continue here" → `session.create`). Taking it back is something the
person does.

## Files

- `skill.py` — the declaration. What it contributes arrives from
  `register_controllers`, because the core exists by then and the router
  is assembled after (`bus.POINT_CORE_SERVICES`).
- `webchat_service.py` — the subscriptions, the forwarding, `_opened`,
  and the spoken-reply contribution.
- `tests/` — the flow driven the way a browser drives it.
