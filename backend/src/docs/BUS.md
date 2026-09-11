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

## Three mechanisms

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

**`handlers_for(type)`.** Answers "who would take a message of this type,
right now". Kept for the three call sites listed under *Open points*
below, and not to be used in new code.

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
| `ui.test_update` | `UI_TEST_UPDATE` | `dict` — one batch of benchmark progress | `system.broadcaster` | `system.ws_notifications` |
| `mail.send` | `MAIL_SEND` | `{"to", "subject", "body_md"}` | `tracking.actuators` (`task.send_mail`) | `mail` |
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

Two frame types are the socket's own and never touch the Bus:
`human_reply` / `human_typing` inbound and `human_prompt` outbound, which
belong to one operator answering one prompt over one connection (see
`talker.human_talker`).

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

**Bounceback.** `publish_with_bounceback(message, sender)` is designed
and not built: the message would be returned to its sender when no
listener is registered for its type, so a producer learns "nobody can do
this" as a delivery rather than as a boolean it must branch on. A
listener would never decline a message per-delivery — subscribing to a
type already declares what it wants, and "handlers may refuse" only
relocates one conditional at the producer into one in every consumer. The
Bus guarantees routing, not semantics: a listener registered for
`output.speech` that produces no audio is a bug in that listener, not a
state the Bus should model. `publish` would stay for genuine
fire-and-forget (`ui.notification`), and its `bool` return would go —
a bool return is an `if` at every call site, and `task.whatsapp()`
currently propagates it into project YAML.

**`handlers_for` is to be removed.** Three call sites remain:
`system/broadcaster.py` (an optimisation, not a capability question),
`tracking/actuators/actuator_set.py` (`task.send_mail`, which
`publish`'s own return value already answers) and
`whatsapp/whatsapp_service.py` (which of two failure notices to send).

**Who takes an `input.text`.** Every `input.text` reaches every
listener, including one converted from a voice note on another channel.
`WebchatService` therefore takes only messages on `NATIVE_CHAT`, the way
`whatsapp` takes only `WHATSAPP_CHAT` ones for `output.text`. A listener
for a type that more than one channel publishes has to say which channel
it serves; the Bus will not guess.
