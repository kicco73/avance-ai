# The Bus

`system/bus.py` is the seam that lets a package reach something it must
not name. A skill that needs a voice note transcribed does not import
`listen`; it publishes `input.audio` and whoever decodes audio in this
build decodes it. A build without that package simply has nobody
registered, and that is a normal outcome rather than an error.

## Not the event dispatcher

`events/` and the Bus look alike and are not. An event there is a *fact
that happened* — `StateChanged`, `AvailabilityChanged` — with no
destination and no answer; anyone may observe it and nothing is expected
to result. A message here is a *delivery*: it has a type, it carries
everything its handler needs, and something is expected to happen to it.

Use `events/` to announce. Use the Bus to hand work over.

## Four mechanisms

**`subscribe(type, listener)` / `publish(message)`.** The delivery path.
`publish` awaits each listener in subscription order; a listener that
raises is logged and the rest still run, so one broken consumer cannot
silence the others. It returns whether anything took the message. Every
producer already runs outside a request, so nothing needs protecting from
a listener that takes two seconds, and ordering is worth more than
concurrency.

**`contribute(point, contributor)` / `collect(point, target)`.** Not
messages: nothing is delivered and nobody is notified. Someone asks,
synchronously, and whoever registered fills in its part of the thing
being assembled. Every contribution point is a response being built while
a caller waits for it, which is why it is synchronous and why `collect`
returns the target so a call site reads as one line.

A contributor that raises takes the collect down with it, and the
contributors after it never run. This is the opposite of how a listener
behaves, and deliberately so: a message is an announcement that has
already happened, while a contribution point assembles the thing its
caller is about to act on — the loader a build will read every automaton
through, the router it will answer requests with, the services it will
wire against. Each has one chance to be right, and half an assembled
target is not a target. Logging the failure and carrying on leaves a
system that runs, answers, and is wrong: a product whose packaged loader
raised in `POINT_AUTOMATON_LOADER` fell back to the database loader
without a word. The exception travels as the skill raised it, unwrapped;
`collect` only adds a log line naming the point and the contributor,
because the traceback on its own points at a lambda in a skill file.

**`publish_with_bounceback(message, sender)`.** The same delivery, with
the message handed back to `sender.bounced(message)` when no listener is
registered for its type — or when it was dropped for looping past
`MAX_CONVERSIONS`. Nothing is asked of the registry beforehand and
nothing is returned to branch on: *undeliverable* is itself a delivery,
so a producer writes what it means once, in a method with a name,
instead of at every call site. `Sender` is a Protocol; the sender is an
object, never a callback.

**A project may refuse a service before the post.** What a project
declared about a service (`project.services`, see
`automaton/project_services.py`) decides *how* a task's post is made:
`optional`/`required` publish normally, `disabled` hands the message
straight to `sender.bounced` — or answers `False` — without touching the
registry. A producer writes the same call either way
(`self._services["mail"].deliver(message, sender)`), and "the operator
left this service out of the build" and "this project said no" reach it
as one and the same answer. This is a decision on the *sending* side and
changes nothing about routing: it is not a listener declining.

A listener may **not** decline a message per delivery. Subscribing to a
type already declares what it wants, and "try to deliver, handlers may
refuse" only moves one conditional at the producer into one in every
consumer. The Bus guarantees routing, not semantics: a listener
registered for `output.speech` that produces no audio is a bug in that
listener, not a state the Bus should model.

**`handlers_for(type)`.** Answers "who would take a message of this type,
right now". Kept for the two call sites listed under *Open points*
below, and not to be used in new code — publish and let it bounce.

## The envelope is the message

`Message` is frozen. `body` is one of its fields, never the whole of it:
a handler that loses the envelope loses which conversation the answer
belongs to.

| Field | Meaning |
| --- | --- |
| `type` | What listeners register for. |
| `body` | The payload. Its shape is per type — see the table below. |
| `username` | Whose exchange this is. |
| `project_id`, `session_id` | Where it belongs, when that is known. |
| `channel` | `native` / `whatsapp` — see `turn/channels.py`. |
| `origin_id` | The channel's own id for the message a person actually sent, unchanged across conversions. On a socket-injected message it is the *connection* id, so an answer goes back to that tab and not to every tab the identity has open. |
| `converted_from` | The type this message was converted from, if it was. |
| `mime` | Media type of `body` where `type` does not imply it. |
| `stream_id` | The interface's own name for one exchange, where it has one — a websocket turn's own id. Correlation, like `origin_id`, which is why it lives here: the body of an `input.text` is the text, and one type must not have two body shapes. |
| `conversions` | How many conversions deep this is. |

`message.converted(type, body, mime)` produces the same message with a
different body: everything that says *which conversation this is* is
preserved by construction. A conversion chain deeper than
`MAX_CONVERSIONS` (4) is dropped and logged — a handler that publishes
the type it consumes would otherwise recur forever.

## Publish and take back

Where a producer needs an answer, it registers a one-shot listener for
the answer's type *before* publishing, filters by `origin_id`, and
unsubscribes in a `finally`. Two of these can be in flight at once and
neither may take the other's answer — which is why the filter is by
origin and not by type alone.

```python
async def take(message: Message) -> None:
    answers[str(message.origin_id)] = message.body

bus.subscribe(OUTPUT_AUDIO_STREAM, take)
try:
    await bus.publish(Message(type=OUTPUT_SPEECH, body=text, ...))
finally:
    bus.unsubscribe(OUTPUT_AUDIO_STREAM, take)
```

Used by `talker/ai_talker.py` (`output.speech` → `output.audio_stream`)
and `whatsapp/whatsapp_service.py` (`input.audio` → `input.text`).

## Messages

| Type | Constant | Body | Published by | Taken by |
| --- | --- | --- | --- | --- |
| `input.audio` | `INPUT_AUDIO` | `bytes`, or an awaitable callable returning them — a voice note nobody decodes is never downloaded | `whatsapp` | `listen.decoder.SpeechDecoder` |
| `input.text` | `INPUT_TEXT` | `str` — what the person said | `system.bus_channel` (client injection), `listen.decoder` (conversion) | `turn.input_listener.TurnInput` (runs the turn — core, whichever channel sent it) |
| `output.text` | `OUTPUT_TEXT` | `str` (markdown) — one chunk of a reply as it is generated, or a whole message from `task.whatsapp()` | `turn.input_listener` (chunks), `tracking.actuators` (`task.whatsapp()`) | `webchat` (forwards to the connection in `origin_id`), `whatsapp` (sends it, when `channel` matches) |
| `output.speech` | `OUTPUT_SPEECH` | `str` — a reply's `[audio]` text | `talker.ai_talker` (wants the audio back), `turn.input_listener` (announces it; webchat forwards it) | `talk`, `webchat` |
| `output.audio_stream` | `OUTPUT_AUDIO_STREAM` | `AudioStream` — `chunks()` yields WAV bytes as they are generated, a fresh iterator per consumer | `talk` | `talker.ai_talker` (one-shot take) |
| `ui.notification` | `UI_NOTIFICATION` | `dict` — a nudge for whoever that identity has open | `tracking.wakeup_service`, `tracking.actuators` | `system.bus_channel` |
| `ui.human_takeover` | `UI_HUMAN_TAKEOVER` | `{"session_id", "project_id"}` | `tracking.actuators.chat_namespace` | `system.bus_channel` |
| `ui.system_warning` | `UI_SYSTEM_WARNING` | `dict` — addressed to a role, so the publisher names each recipient | `project.health_notifications` | `system.bus_channel` |
| `ui.progress` | `UI_PROGRESS` | `dict` — one batch of job progress | `system.broadcaster` | `system.bus_channel` |
| `turn.started` | `TURN_STARTED` | `{"session_id"}` — a reply is being composed | `turn.input_listener` | `webchat` |
| `turn.ended` | `TURN_ENDED` | `dict` — the turn's whole result, so a consumer that ignored the chunks has the finished answer. `reply` is every assistant message this exchange produced, in order: whatever the state owed before a turn could run (see `TurnService.prepare_user_initiated_turn`) followed by the turn's own | `turn.input_listener` | `webchat` |
| `turn.failed` | `TURN_FAILED` | `{"message", "detail", "code", "reply"}` — the code is core's, the wording a channel's own; `reply` is what the preparation persisted before the turn was refused, which the person is owed either way (empty when there was none) | `turn.input_listener` | `webchat` |
| `turn.tool` | `TURN_TOOL` | `dict` — one tool call, `phase` telling start from result | `turn.input_listener` | `webchat` |
| `mail.send` | `MAIL_SEND` | `{"to", "subject", "body_md"}` | `tracking.actuators` (`task.send_mail`, with bounceback) | `mail` |
| `turn.started` | `TURN_STARTED` | — | — | — |
| `turn.ended` | `TURN_ENDED` | — | — | — |
| `turn.failed` | `TURN_FAILED` | — | — | — |
| `turn.tool` | `TURN_TOOL` | — | — | — |

The four `turn.*` constants are **wire frame names only**: nothing
publishes or subscribes to them. They live here so the socket and the Bus
cannot drift apart in how a turn's facts are named.

### What a client may inject

`CLIENT_INJECTABLE` is the allowlist of types a browser may put on the
Bus over its socket — today `input.text` alone. The wire carries these
very names, so without the list the socket would be an open injection
point: a browser could publish an internal type and find listeners for
it. A client speaks as a person, and a person says things.

Outbound works the same way. `bus_channel.WEB_FORWARDED` is the
allowlist of types that may leave the Bus for a browser, and that filter
is the whole of what the socket does: a frame reaching the client is the
message that was published, under its own type. `ui.notification` arrives
as `ui.notification`. A turn's own frames (`output.text`,
`output.speech`, `turn.*`) go out under the same names, so a listener and
a browser read the same message.

### A browser registers, like any other listener

Being connected is not being subscribed. A client asks for the types it
wants with a `subscribe` frame and drops them with `unsubscribe`:

```json
{"type": "subscribe", "events": ["ui.notification", "ui.progress"]}
{"type": "unsubscribe", "events": ["ui.progress"]}
```

Both name types out of `WEB_FORWARDED` and nothing else — a type outside
it is refused and logged, since registering for one would otherwise be a
way to *read* an internal type, the same hole `CLIENT_INJECTABLE` closes
on the way in. The registration lives on the connection, so a browser
restates it on every (re)connection; nothing is remembered for a socket
that went away.

`push_event` is the delivery that honours it, and the only path a Bus
message takes to a browser (`_forward_to_web`). Plain `push` stays the
*addressed* path — a frame that belongs to this socket rather than to
the Bus (`human_prompt`), sent to an identity's connections whether or
not they registered for anything.

Five frame types are the socket's own and never touch the Bus:
`subscribe` / `unsubscribe` inbound (the registration above),
`human_prompt` outbound, `human_reply` / `human_typing` inbound. The
last three belong to one operator answering one prompt over one
connection (see `talker.human_talker`). An inbound one is honoured only
when the connection it arrived on belongs to the identity the prompt was
actually sent to — those frames carry a `session_id`, which any other
signed-in user could name just as well. See *Open points*: the outbound
half is meant to become a message.

## Contribution points

| Point | Constant | Target | Asked by | Filled by |
| --- | --- | --- | --- | --- |
| `api.state` | `POINT_API_STATE` | the `GET /api/core/state` payload | `system.api_state_controller` | `talk`, `listen`, `build` |
| `config.services` | `POINT_CONFIG_SERVICES` | the public services snapshot | `config.py`, `system.config_services` | `talk`, `listen`, `mail`, `whatsapp`, `testing`, `build` |
| `http.controllers` | `POINT_HTTP_CONTROLLERS` | the list of controllers to route | `controller.py` | `talk`, `listen`, `webchat`, `whatsapp`, `avance_platform`, `testing`, `build` |
| `core.services` | `POINT_CORE_SERVICES` | the composed core, offered to whoever asks | every skill | `main.py`, `testing` |
| `automaton.loader` | `POINT_AUTOMATON_LOADER` | which loader answers "give me this project's automaton" | `main.py` | `avance_platform`, `product` |
| `turn.spoken_reply` | `POINT_SPOKEN_REPLY` | `SpokenReply` — what the project declared, and whether this turn wants audio at all; a contributor that can speak calls `ask()` | `tracking.tracking_processor` | `talk` |

Every `<skill>_enabled` field in the state payload is that skill's own
contribution, `talk_enabled` included. It used to be the exception:
`system/api_state_controller.py` computed it from `services["talk"]` and
a `talk_configured()` helper, and `TrackingService` took a `talk_enabled`
argument threaded down from `main.py` to be combined with the same name
again. Core knew the name of a package a build is meant to be able to
ship without, in three places, to decide one thing. `turn.spoken_reply`
is that one thing asked instead of answered: nobody registered means
nothing speaks, which is the right conclusion for a build without the
package and one core never has to reach by naming it.

`core.services` runs the other way from the rest: a skill starts at boot,
long before `Db`/`TurnService`/`SchedulerService` exist, so it cannot be
handed them as arguments. The core contributes itself once composed, and
a skill collects it from inside work that runs later — a
`http.controllers` contributor, or the first message it handles. Nothing
declares a dependency and nothing orders anything; the only rule is that
a collect must not run before `main.py` has contributed.

A skill may contribute to it too, and `testing` does: the service it
builds for itself (`TestingService`) is offered back under its own name,
so anything composed after it — the test harness above all — finds it
the same way it finds `db` or `turn_service`, instead of the skill
having to hand it somewhere. Only the skill that built a service can
offer it, and only after `register_controllers` has run.

## Availability

A package declares what it is at `config.services`; that, not the
listener registry, is what answers "does this installation have a TTS
provider" (see `system/config_services.py:talk_configured`). Whether a
*message* can be handled is answered by publishing it.

## Open points

**`publish`'s `bool` return is to go.** It exists only for callers that
predate bounceback. A bool return is an `if` at every call site, and
`task.whatsapp()` propagates its own all the way into project YAML.
`publish` stays for genuine fire-and-forget (`ui.notification`, where
whether anything is listening is not the producer's business); every
caller that wants an answer moves to `publish_with_bounceback`.

**`handlers_for` is to be removed.** Two call sites remain:
`system/broadcaster.py` (an optimisation, not a capability question —
the fix is a deferred body, so that wanting the message is what costs)
and `whatsapp/whatsapp_service.py` (which of two failure notices to
send, decided after the fact).

**Reaching a human over any medium.** `HumanRelay` is already a
medium-agnostic Protocol and `HumanTalker` names no transport, but
`TrackingService.set_human_talker_factory` holds a single slot written at
boot by `webchat/skill.py` — one slot, one medium, chosen before anyone
knows who the operator is. The intent is to publish `human.prompt`
addressed to the operator and let any medium that can reach them answer:
several may answer at once and the first reply wins (the take-back
filters by prompt id), and "nobody can reach this person" becomes the
bounce that today is webchat's local `HumanNotConnectedError`. What the
Bus does not solve is the inbound leg — an operator's WhatsApp reply
arrives as an ordinary text from a number, and nothing yet records that
this number is operating that session. `human_typing` stays a frame: a
per-connection liveness signal with no other possible producer.

**Who takes an `input.text`.** Core does: `turn.input_listener.TurnInput`
is the one listener, whichever channel published the message and whether
it arrived as text or was converted from a voice note. It runs the turn
and publishes every frame the turn produces; a channel forwards the ones
addressed to it and runs nothing.

It used to be the channels, each on its own. `WebchatService` took only
messages on its own channel and ran the turn itself (`webchat/ws_turn.py`),
`whatsapp` did the same work its own way, and they did not merely
duplicate it — they disagreed: one streamed and trusted the turn's
result, the other reassembled an answer from the database. The
discriminator is gone with the duplication.

Nothing in `turn/` names a channel. The message says which one it came
from (`channel`, stamped by the publisher — `system.bus_channel` stamps
whatever the interface listening on it claimed at boot, see
`BusChannel.owned_by`), and the frames carry the same `origin_id` and
`stream_id` back out, which is how a channel picks out its own answer
without core knowing who is listening. A channel's name is the name of
the skill that is that channel: `webchat`, `whatsapp`.

`Session().for_sender(username, role=..., channel=...)` is how a listener
gets a context. It does not inherit one: `publish` awaits each listener
in the publisher's own task today, so ContextVars happen to propagate,
but that is already false for `system/broadcaster.py` (another thread,
another loop) and `tracking/wakeup_service.py` (a scheduled job), both of
which pass `username` on the Message because they had to. `role` is
looked up from the database, never taken off the wire — a channel must
not be able to declare its own caller's privileges.

**Order, when a listener publishes what a turn produces.** It used to be
free: `on_metadata` is synchronous end to end and the frames went straight
onto a websocket, so nothing could be overtaken. Publishing is not
synchronous, and a task per frame would put a stream of chunks on the wire
in whatever order the loop reached them. `TurnInput` queues them as they
are made — synchronously, from that same callback — and one task drains
the queue, awaiting each publish before it takes the next.
