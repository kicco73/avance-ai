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

**`publish_with_bounceback(message, sender)`.** The same delivery, with
the message handed back to `sender.bounced(message)` when no listener is
registered for its type — or when it was dropped for looping past
`MAX_CONVERSIONS`. Nothing is asked of the registry beforehand and
nothing is returned to branch on: *undeliverable* is itself a delivery,
so a producer writes what it means once, in a method with a name,
instead of at every call site. `Sender` is a Protocol; the sender is an
object, never a callback.

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
| `input.text` | `INPUT_TEXT` | `str` — what the person said | `system.ws_notifications` (client injection), `listen.decoder` (conversion) | `webchat.WebchatService` (starts a turn, on its own channel only), `whatsapp` (one-shot take) |
| `output.text` | `OUTPUT_TEXT` | `str` (markdown) | `tracking.actuators` (`task.whatsapp()`) | `whatsapp` (sends it, when `channel` matches) |
| `output.speech` | `OUTPUT_SPEECH` | `str` — a reply's `[audio]` text | `talker.ai_talker` (wants the audio back), `webchat.ws_turn` (only warms the store) | `talk` |
| `output.audio_stream` | `OUTPUT_AUDIO_STREAM` | `AudioStream` — `chunks()` yields WAV bytes as they are generated, a fresh iterator per consumer | `talk` | `talker.ai_talker` (one-shot take) |
| `ui.notification` | `UI_NOTIFICATION` | `dict` — a nudge for whoever that identity has open | `tracking.wakeup_service`, `tracking.actuators` | `system.ws_notifications` |
| `ui.human_takeover` | `UI_HUMAN_TAKEOVER` | `{"session_id", "project_id"}` | `tracking.actuators.chat_namespace` | `system.ws_notifications` |
| `ui.system_warning` | `UI_SYSTEM_WARNING` | `dict` — addressed to a role, so the publisher names each recipient | `project.health_notifications` | `system.ws_notifications` |
| `ui.progress` | `UI_PROGRESS` | `dict` — one batch of job progress | `system.broadcaster` | `system.ws_notifications` |
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

Outbound works the same way. `ws_notifications.WEB_FORWARDED` is the
allowlist of types that may leave the Bus for a browser, and that filter
is the whole of what the socket does: a frame reaching the client is the
message that was published, under its own type. `ui.notification` arrives
as `ui.notification`. A turn's own frames (`output.text`,
`output.speech`, `turn.*`) go out under the same names, so a listener and
a browser read the same message.

Three frame types are the socket's own and never touch the Bus:
`human_prompt` outbound, `human_reply` / `human_typing` inbound. They
belong to one operator answering one prompt over one connection (see
`talker.human_talker`). An inbound one is honoured only when the
connection it arrived on belongs to the identity the prompt was actually
sent to — those frames carry a `session_id`, which any other signed-in
user could name just as well. See *Open points*: the outbound half is
meant to become a message.

## Contribution points

| Point | Constant | Target | Asked by | Filled by |
| --- | --- | --- | --- | --- |
| `api.state` | `POINT_API_STATE` | the `GET /api/state` payload | `avance_platform.platform_controller` | `listen` |
| `config.services` | `POINT_CONFIG_SERVICES` | the public services snapshot | `config.py`, `system.config_services` | `talk`, `listen`, `mail`, `whatsapp`, `testing` |
| `http.controllers` | `POINT_HTTP_CONTROLLERS` | the list of controllers to route | `controller.py` | `talk`, `listen`, `webchat`, `whatsapp`, `avance_platform`, `testing` |
| `core.services` | `POINT_CORE_SERVICES` | the composed core, offered to whoever asks | every skill | `main.py` |
| `automaton.loader` | `POINT_AUTOMATON_LOADER` | which loader answers "give me this project's automaton" | `main.py` | `avance_platform`, `product` |

`core.services` runs the other way from the rest: a skill starts at boot,
long before `Db`/`TurnService`/`SchedulerService` exist, so it cannot be
handed them as arguments. The core contributes itself once composed, and
a skill collects it from inside work that runs later — a
`http.controllers` contributor, or the first message it handles. Nothing
declares a dependency and nothing orders anything; the only rule is that
a collect must not run before `main.py` has contributed.

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

**Who takes an `input.text`.** Every `input.text` reaches every
listener, including one converted from a voice note on another channel.
`WebchatService` therefore takes only messages on `NATIVE_CHAT`, the way
`whatsapp` takes only `WHATSAPP_CHAT` ones for `output.text`. A listener
for a type that more than one channel publishes has to say which channel
it serves; the Bus will not guess.
