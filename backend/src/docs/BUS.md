# The Bus — the messages there are

Read off the code (`bus.subscribe` / `bus.publish`), not off a design.
Update it in the commit that changes the bus.

## The rule

**The first segment is the scope, never the direction.** `session.*` is
about one conversation, `state.*` about where it stands, `input.*` about
what a person did, `output.*` about what is being said back, `ui.*` about
something an interface may want to show.

**Requests are verbs, announcements are nouns.** A client asks
`session.enter`; the server answers `session.info`. Nothing is named for
who sends it.

Every body is a **dictionary**. On the wire a frame is that dictionary
with its own `type` in it: one shape to read, no wrapper.

## What a person does

| Type | Body | Published by | Received by |
| --- | --- | --- | --- |
| `input.text` | `{text}` — what the person asks | `system/bus_channel.py` (a browser frame), `whatsapp/turn_exchange.py`, `listen/decoder.py` (converted from `input.audio`) | `turn/input_listener.py`, `whatsapp/inbound_voice_note.py` (subscribed for one exchange) |
| `input.button` | `{id}` — one of the choices, taken. Same road as `input.text`, so the two cannot overtake each other | `system/bus_channel.py` | `turn/input_listener.py` |
| `input.audio` | `{audio}` — the bytes, or a callable that fetches them | `whatsapp/inbound_voice_note.py` | `listen/decoder.py` |
| `input.reaction` | `{assistant_message_id, reaction}` — the person's own reaction to a message. The model's reaction to theirs is `output.reaction`: two facts about two different messages | `system/bus_channel.py` | `turn/input_listener.py` |

## One conversation

Addressed by `session_id`, except entering and creating: those name a
`project_id`, because the session is what they are asking for.

| Type | Direction | Body |
| --- | --- | --- |
| `session.enter` | client → server | `{session_type}` — `live`, `test` or `preview`. I am showing a conversation of this kind for this project: give me the active one, or make one. It also says this connection is now watching that session |
| `session.create` | client → server | `{session_type}` — make a new one regardless, closing whatever was active |
| `session.exit` | client → server | `{}` — I have stopped watching. Handled by the socket itself and never published: who watches what is the socket's own bookkeeping |
| `session.recall` | client → server | `{before, limit}` — bring back what was said earlier. What is on screen when a conversation opens arrives without asking |
| `session.terminate` | client → server | `{}` — the person closes it |
| `session.speak` | client → server | `{enabled}` — speak, or stop speaking, in this conversation: whether the model is asked for the spoken version of its reply |
| `session.info` | server → client | `{state, services, audio, current, channel, project_id}` — which conversation this is and everything describing it |
| `session.messages` | server → client | `{messages}` — what was said |
| `session.opened` | server → server | `{}` — this conversation has just been opened and has said nothing. Published by `turn/input_listener.py` after the whole announcement, and **only when the transcript it just announced was empty**. `webchat/conversation_opener.py` answers it with whatever the state has to say first. Never reaches a client |
| `session.ended` | server → client | `{reason}` — closed, by the person or by the server itself (`channel-switch`, `force-new-session`, `revision-invalid`). Published from the one place every closure passes through, `SessionManager.close_session` |
| `session.blocked` | server → client | `{reason, detail}` — there is no conversation to be had: `paused`, `terms`, `no_project`, `no_channel`. A refusal, not a failure: whoever shows a chat shows a different screen for each |
| `session.taken_over` | server → client | `{project_id}` — handed to a person. Named for the session because that is what it is about, but delivered to that identity's connections: the point of it is to reach an operator who is not in the conversation yet |

## What is said back

| Type | Body | Published by | Received by |
| --- | --- | --- | --- |
| `output.text_stream` | `{text}` — a piece of a message as it is written. **Empty** means the writing has started and nothing is readable yet | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `output.text` | `{text, assistant_message_id, timestamp}` — a whole message. The **last** one is the reply, and it is what says the exchange is over. The text to be spoken is its own message (`output.speech`), never a field of this | `turn/input_listener.py`, `tracking/actuators/actuator_set.py` (`task.whatsapp`) | `webchat/webchat_service.py`, `whatsapp/whatsapp_service.py`, `whatsapp/turn_exchange.py` |
| `output.speech` | `{text}` — the spoken version of the reply, written by the model alongside it. It arrives while the reply is still being written, and a later one replaces the earlier | `turn/input_listener.py`, `talker/ai_talker.py` (asks for it) | `talk/skill.py`, `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |
| `output.audio_stream` | `{stream}` | `talk/skill.py` | `talker/ai_talker.py` (for one exchange) |
| `output.tool` | `dict` — one tool call; `phase` tells its two halves apart | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `output.reaction` | `{user_message_id, reaction}` — the model reacted to **that** message | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `output.error` | `{message, detail, code}` — in place of the reply. Only for things that went wrong: a conversation that cannot be had is `session.blocked` | `turn/input_listener.py` | `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |
| `state.changed` | `{state, new_state, triggered_action}` — said only **when it moves**: a reader keeps the last one it was told | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `state.buttons` | `{actions}` — what can be done now, and the **only** place the choices are: no state payload carries them | `turn/input_listener.py` | `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |

## Notifications and tools

| Type | Body | Published by | Received by |
| --- | --- | --- | --- |
| `ui.notification` | `dict` | `tracking/wakeup_service.py`, `tracking/actuators/action_task.py`, `tracking/actuators/chat_namespace.py` | `system/bus_channel.py` |
| `ui.system_warning` | `dict` | `project/health_notifications.py` | `system/bus_channel.py` |
| `ui.progress` | `dict` | `system/broadcaster.py` | `system/bus_channel.py` |
| `tool.send_mail` | `{to, subject, body_md}` | `tracking/actuators/actuator_set.py` (`task.send_mail`) | `mail/skill.py` |

## One request, one reply

There are no turns: a person asks, something answers. `input.text` is the
request; the coalescer (`turn/input_listener.py`, `TurnInput._requests` — in
memory, one entry per session) decides how many requests become one reply:
the consecutive `input.text` requests that piled up while the previous reply
was being written are answered together, anything else on its own (see
`PROJECT_SPECS.md` §0.1). What comes back:

```text
output.text_stream {text: ""}   it has started writing, nothing readable yet
output.tool                     it is using a tool
output.text_stream {text: "…"}  the pieces, as they come
output.text                     a message the state owed before it could answer
output.reaction                 it reacted to what the person said
state.changed                   only if the conversation moved
state.buttons                   what can be done now
output.text                     the reply — and the exchange ends here
```

There is no separate terminal message: **the reply is the end**. When
something goes wrong, `output.error` arrives in its place.

A reader does not wait for a final payload; it assembles what was
published. `conftest.chat_turn` does the same, so a test reads what a
browser really reads.

Entering a conversation answers in this order, and the order matters —
what frames a message comes before the message:

```text
session.info                    which conversation this is, and where it stands
session.messages                what was said
state.buttons                   what can be done now
                                — the announcement ends here, and `session.opened`
                                  goes out on the Bus
output.text_stream / output.text  the opening message, if the state has one
```

Opening is a **reaction to the announcement**, not a kind of request.
`session.enter` and `session.create` never join the queue of requests:
that queue exists to fix the order of what a *person* says, has nothing
to fix for a message nobody asked for, and every type it had to
recognise as "not really typed text" was one more thing to get wrong —
`session.create` was answered with «Message cannot be empty» for exactly
that reason.

And the core does not decide whether a conversation should speak. It
resolves the session, announces it, and says `session.opened` when what
it announced was empty — a fact it has in hand, because it read the
transcript to send it. A **chat** answers that event
(`webchat/conversation_opener.py`); a channel that shows no chat does
not. Had the event been published on every `session.enter`, whoever
listened would have had to work out whether the conversation had already
spoken, and every reload would have been greeted again.

Nobody works that out anywhere now: hearing it, a chat asks for the
message (`TurnService.open_conversation`), which runs the turn without
asking itself whether it should. A channel that opens its own
conversations (`whatsapp/whatsapp_service.py`) decides the same way the
core does — on the transcript it has just read — and asks for the same
thing.

## Who is told what

An **answer** goes back to the connection that asked: the request carried
its id (`origin_id`), and `webchat/webchat_service.py` sends the frame
there.

An **announcement** — a session closed from elsewhere, a conversation
handed to a person — has no request behind it and no connection to answer
to. It goes to whoever is *watching* that conversation: a connection
starts watching when it is told which session it entered, and stops on
`session.exit`.

A **notification** (`ui.*`, and `session.taken_over`) goes to an
identity's connections, and only to those that registered for its type.

## Websocket frames (`/api/core/bus`)

These never reach the bus: they belong to the socket.

| Frame | Direction | What it does |
| --- | --- | --- |
| `session.exit` | browser → server | Stop telling me about this conversation |
| `subscribe` / `unsubscribe` | browser → server | Register the types this connection wants (`CLIENT_REGISTRABLE`) |
| `ping` / `pong` | browser ↔ server | Keeping it alive |
| `human_prompt` | server → browser | A turn is waiting on a person. Only to connections registered for it |
| `human_reply` / `human_typing` | browser → server | The operator's answer, and their "typing" |
| `switched_to_other_client` | server → browser | Another connection of the same identity took the channel |

Everything a client may put **on** the bus is `CLIENT_INJECTABLE`: the
`input.*` messages and the `session.*` requests. The wire uses those very
names — a frame is not translated on the way in — so without that list
the socket would be an open injection point.

A frame is flat: the envelope (`type`, `session_id`, `project_id`) and
the body are one object, and the envelope is read off it and removed.
That is why the kind of session travels as `session_type` and not as
`type` — `type` is the name of the message itself and never survives the
trip into the body.

## What a connection may register for

`CLIENT_REGISTRABLE` = `WEB_FORWARDED` (`ui.notification`,
`session.taken_over`, `ui.system_warning`, `ui.progress`) + `human_prompt`.

A type outside that list is refused: registering would otherwise be a way
to read an internal type. The registration lives on the connection and
has to be declared again on every reconnection.

Registering is **how a connection says what it is**: `human_prompt` goes
to whoever asked for it and to nobody else, and that is what makes one tab
the one answering as a person. A prompt already waiting when a connection
registers is delivered to it then.

## Contribution points

Not messages: somebody asks, synchronously, and whoever registered fills
in their part.

| Point | What is assembled | Asked by | Filled by |
| --- | --- | --- | --- |
| `api.state` | the payload of `GET /api/core/state` | `system/api_state_controller.py` | `talk`, `listen`, `build` |
| `config.services` | the public snapshot of the services | `config.py` | `talk`, `listen`, `mail`, `whatsapp`, `testing`, `build` |
| `http.controllers` | the controllers to mount | `controller.py` | `talk`, `listen`, `whatsapp`, `avance_platform`, `testing`, `build` |
| `core.services` | the assembled core | every skill | `main.py`, `testing` |
| `automaton.loader` | which loader answers "give me this automaton" | `main.py` | `avance_platform`, `product` |
| `turn.spoken_reply` | `SpokenReply` — `want()` from whoever runs the interface, `ask()` from whoever can speak | `tracking/tracking_processor.py` | `talk`, `webchat`, `whatsapp` |
| `session.services` | `SessionServices` — `offers(name, installed)`: what each service can do for **one** conversation. The session's own project can only narrow the server's switch | `turn/turn_service.py` | `talk`, `listen` |

## Queued work

**`test`, `preview` and `imported` do not belong in the core.** They are
session types, and the four `SessionTypeStrategy` classes plus the
`_STRATEGIES` dictionary that registers them by hand are in
`turn/sessions/session_type_strategy.py`, with `TurnService` naming them
one by one. They should be contributions: `test` and `preview` from the
authoring skill, `imported` from `testing`, leaving the core with `live`
alone — a build without those skills should not know those types exist.
`session.enter {session_type}` does not make this harder: the type is already a
string the registry validates.

**A `preview` session should destroy itself when it closes.** Today the
client does it (`appStorePreviewStore.stopPreviewSession`), so a client
that dies first leaves the session behind. `test` sessions must **not**:
the editor lists them and the benchmark reads every type.
