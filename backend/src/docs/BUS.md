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

**Who publishes a type and who listens for it is not part of the
vocabulary.** That is the whole point of the seam: a producer says what
it means once and a build decides who is there to hear it. **Nobody
registered is a normal outcome, not an error** — it is what a build
without that package looks like, and `publish_with_bounceback` makes
undeliverable itself a delivery so no caller has to branch on it. A
package that publishes or subscribes to any of these says so in its own
documentation; this page says only what the messages are.

## What a person does

| Type | Body |
| --- | --- |
| `input.text` | `{text}` — what the person asks |
| `input.button` | `{id}` — one of the choices, taken: an action's `name`, or `choice:<key>:<index>` for the option of a `choice` env key (see `state.buttons`). Same road as `input.text`, so the two cannot overtake each other. A `choice:` id nobody offers is `output.error` with code `choice_unavailable`; one whose trigger does not answer publishes nothing at all |
| `input.audio` | `{audio}` — the bytes, or a callable that fetches them. Whoever transcribes converts it to `input.text` on the same envelope; the publisher never re-publishes the transcript itself |
| `input.reaction` | `{assistant_message_id, reaction}` — the person's own reaction to a message. The model's reaction to theirs is `output.reaction`: two facts about two different messages |

## One conversation

Addressed by `session_id`, except entering and creating: those name a
`project_id`, because the session is what they are asking for.

**`session.enter` and `session.create` are the only way to reach a
session at all**, and that holds for every kind of it: live, test and
preview alike go through `turn/input_listener.py`, which is the single
door. No route resolves or creates one. What HTTP still has is the
administration of sessions nobody is in: listing them, deleting one,
annotating one. Even reading a transcript opens nothing
(`TurnService.read_history`).

| Type | Direction | Body |
| --- | --- | --- |
| `session.enter` | client → server | `{session_type}` — `live`, `test` or `preview`. I am showing a conversation of this kind for this project: give me the active one, or make one. It also says this connection is now watching that session. A channel with no screen enters too, once per inbound message |
| `session.create` | client → server | `{session_type}` — make a new one regardless, closing whatever was active. What a channel asks for when the session it was given belongs to another one (`channel-switch`) |
| `session.exit` | client → server | `{}` — I have stopped watching. Handled by the socket itself and never published: who watches what is the socket's own bookkeeping |
| `session.recall` | client → server | `{before, limit}` — bring back what was said earlier. What is on screen when a conversation opens arrives without asking |
| `session.terminate` | client → server | `{}` — the person closes it |
| `session.speak` | client → server | `{enabled}` — speak, or stop speaking, in this conversation: whether the model is asked for the spoken version of its reply |
| `session.info` | server → client | `{state, services, audio, current, channel, project_id}` — which conversation this is and everything describing it. `current` says the session is the one its type's active slot holds, **never that you may write to it**: writability is that AND `channel` being your own (see "Whose conversation it is") |
| `session.messages` | server → client | `{messages}` — what was said |
| `session.opened` | server → server | `{}` — this conversation has just been opened and has said nothing. Published by `turn/input_listener.py` after the whole announcement, and **only when the transcript it just announced was empty**. Whether a conversation then speaks is the answering channel's decision, not the core's. Never reaches a client |
| `session.exhausted` | server → server | `{}` — the turn just published ended in a state with no outgoing action: the conversation has nowhere left to go. Queued by `Outbound.ran` after the turn's last frame, whoever ran the turn (a person's message, a choice taken, the greeting on opening), so it is never ahead of what that turn said. The core closes the session on it, reason `final-state`. Never reaches a client |
| `session.ended` | server → client | `{reason}` — closed, by the person or by the server itself (`channel-switch`, `force-new-session`, `revision-invalid`, `final-state`). A state meant to be chatted in forever declares a self-loop with `trigger: "True"` and is not final. Published from the one place every closure passes through, `SessionManager.close_session`. It is also the last thing said about that session: whoever was watching it stops, whoever held it forgets it |
| `session.blocked` | server → client | `{reason, detail}` — there is no conversation to be had: `paused`, `terms`, `no_project`, `no_channel`. A refusal, not a failure: whoever shows a chat shows a different screen for each, and a channel with no screen says a sentence for each |
| `session.taken_over` | server → client | `{project_id}` — handed to a person. Named for the session because that is what it is about, but delivered to that identity's connections: the point of it is to reach an operator who is not in the conversation yet |

## What is said back

| Type | Body |
| --- | --- |
| `output.text_stream` | `{text}` — a piece of a message as it is written. **Empty** means the writing has started and nothing is readable yet |
| `output.text` | `{text, assistant_message_id, timestamp}` — a whole message. The **last** one is the reply. The text to be spoken is its own message (`output.speech`), never a field of this. One with **no `session_id`** belongs to no conversation and is addressed to a recipient instead: whichever channel can carry it does, and nobody carrying it is the honest answer that it was not sent |
| `output.speech` | `{text}` — the spoken version of the reply, written by the model alongside it. It arrives while the reply is still being written, and a later one replaces the earlier |
| `output.audio_stream` | `{stream}` — the synthesized audio, for one exchange |
| `output.tool` | `dict` — one tool call; `phase` tells its two halves apart |
| `output.reaction` | `{user_message_id, reaction}` — the model reacted to **that** message |
| `output.chart` | `{title, series}` — `series` is `[{line, value}]`. Published by `chat.chart(title, series)` (`tracking/actuators/chat_namespace.py`), reachable only from an action's own `on-exit:` script. Delivered like `output.text`, through the per-session "who is watching" path, so it reaches only a connection showing that conversation — unlike `output.drive`, which goes to an identity's registered connections instead |
| `output.progress` | `{title, percentage}` — published by `chat.progress(title, percentage)` (`tracking/actuators/chat_namespace.py`), reachable only from an action's own `on-exit:` script. Delivered like `output.chart`: per-session "who is watching" only, never the identity-wide broadcast `ui.progress` (`system/broadcaster.py`) uses — the two are unrelated, one turn-scoped, one a user-wide bar |
| `output.error` | `{message, detail, code}` — in place of the reply. Only for things that went wrong: a conversation that cannot be had is `session.blocked` |
| `state.changed` | `{state, from_state, new_state, triggered_action}` — said only **when it moves**: a reader keeps the last one it was told. `from_state` is where it moved from, so a listener can tell a real transition (`from_state != new_state`) from a self-loop |
| `env.changed` | `{key, value}` — one env key an action wrote, one message per key: whoever cares that a key moved does not care how many others moved with it. Same envelope as `state.changed` |
| `state.buttons` | `{actions}` — what can be done now, and the **only** place the choices are: no state payload carries them. The pressable actions first, then — for every `choice` env key a trigger of the state reads — one entry per current option, named `choice:<key>:<index>` with the option as its `ui_button`/`ui_label` and `target` `""`; pressing one sends that `name` back as `input.button` unchanged |
| `turn.translation` | `{key, text, translation, src_lang, dst_lang}` — one label whoever contributed to `turn.translatable_labels` asked translated, translated by the same `translations` channel a turn's own manual button labels ride (`tracking/tracking_processor.py`). `key` is the context the contributor gave (an env key's name, say), returned unchanged so a listener can match its own contribution. `src_lang`/`dst_lang` are this turn's own answer from `LangPrompt` (`tracking/prompt.py`) — the full locale tag (IETF BCP 47, e.g. `it-IT`) the labels were authored in and the one the user's last message is written in; empty when the model's answer was missing, unparseable, or not a full locale tag (a bare language code is rejected too). Neither is persisted anywhere: a conversation's language is free to change turn to turn, so this is asked fresh every time, never assumed from an earlier turn. One message per label, same reason `env.changed` is one per key: whoever asked for one label does not care about the others. Published once the reply is generated, so whoever contributed reads the answer off the Bus instead of it being threaded back as a return value. Never reaches a client |

## Notifications and tools

| Type | Body | Published by |
| --- | --- | --- |
| `ui.notification` | `dict` | `tracking/actuators/action_task.py` — with `project_id` and, for an immediate run, `session_id` on the envelope, so a client can tell whether the script is about the conversation it has open — `tracking/actuators/chat_namespace.py`, and any installed skill that moves a conversation nobody is speaking in |
| `task.started` | `{key}` | `tracking/actuators/action_task.py`, as the first act of a scheduled `task:` script. `key` is the task's own key. Envelope: the task's `username`, its `project_id`, and its `session_id` — `None` for a deferred call |
| `task.ended` | `{key, result, error}` | `tracking/actuators/action_task.py`, once that script is over for good. `result` is the JS text its snippet-producing statements built (`None` if the script never ran); `error` names every statement that raised, one per line, or is `None` when none did. Same `key` and same envelope as `task.started` |
| `ui.progress` | `dict` | `system/broadcaster.py` |
| `tool.send_mail` | `{to, subject, body_md}` | `tracking/actuators/actuator_set.py` |
| `output.drive` | `{path}` — `None` for a bulk clear | `tracking/actuators/drive_namespace.py`, after every `drive.write(...)` (`path` set to the written path), and `turn/sessions/session_manager.py`'s `clear_drive_of_type`, when a new test session clears a previous test session's drive (`path: None` — the receiving end already treats any `output.drive` as "refetch the listing", so no per-path detail is needed here) — `output.*` for what it names, delivered like the rest of this table (an identity's registered connections, envelope `project_id`/`session_id`), never through the per-session "who is watching" path `output.text`/`state.changed` use |

## One request, one reply

There are no turns: a person asks, something answers. `input.text` is the
request; the coalescer (`turn/input_listener.py`, `TurnInput._requests` —
in memory, one entry per session) decides how many requests become one
reply: the consecutive `input.text` requests that piled up while the
previous reply was being written are answered together, anything else on
its own (see `PROJECT_SPECS.md` §0.1). What comes back:

```text
output.text_stream {text: ""}   it has started writing, nothing readable yet
output.tool                     it is using a tool
output.text_stream {text: "…"}  the pieces, as they come
output.text                     a message the state owed before it could answer
output.reaction                 it reacted to what the person said
state.changed                   only if the conversation moved
output.text                     the reply
state.buttons                   what can be done now — and the exchange ends here
```

The reply is not the last frame: `state.buttons` is, always published right
after it, so a reader has both the answer and what it may do next before it
treats the exchange as over. When something goes wrong, `output.error`
arrives in place of the reply, with no `state.buttons` after it.

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

**The core does not decide whether a conversation should speak.** It
resolves the session, announces it, and says `session.opened` when what
it announced was empty — a fact it has in hand, because it read the
transcript to send it. Whoever answers that event asks for the message
(`TurnService.open_conversation`), which runs the turn without asking
itself whether it should. Had the event been published on every
`session.enter`, whoever listened would have had to work out whether the
conversation had already spoken, and every reload would have been greeted
again. Nobody works that out anywhere now.

A listener that answers it must check the channel first, or it greets a
conversation it does not serve. A listener may also answer nothing at
all: a channel that is not written to until its owner writes loses
nothing by it — `TurnService.prepare_user_initiated_turn` still says what
the state owed, as an `output.text` before the answer to the first thing
the person says.

## Whose conversation it is

A **live session belongs to one channel at a time** (`CoreSession.channel`,
fixed at creation — see `turn/channels.py`), and channels contend for it.
The rule they read is one line, and neither the core nor a client can
apply it alone:

> **writable = `current` AND `channel` is mine.**

`current` on `session.info` says the session is the one its type's active
slot holds and nothing more; `TurnService` deliberately does not combine
the two, because it does not know which channel is asking. The channel
does, and what it should then do follows from **why its `session.enter`
fired**:

- An entry caused by the person **acting** — they wrote, so the entry
  exists — claims the conversation on the spot: `session.create`, which
  closes the other one (`channel-switch`) and opens one of its own.
- An entry that fires **on its own** — every reload, every reconnection
  of a socket — must stand down, or two channels would rally the session
  between them with nobody having done anything. It narrows `current` to
  `false` on the way out and shows the conversation read-only; taking it
  back is something the person does (`session.create`).

Whoever the person *acts* on wins. The consequence is that alternating
between two channels gives one session per alternation — a transcript is
never interleaved, which is the guarantee, not a bug in it.

The guarantee is not either disposition, though: it is
`LiveSessionStrategy.is_valid_write_target`, which refuses a write from
the wrong channel with `session_channel_mismatch` / `session_superseded`.
The two above are how a channel finds out *before* being refused.

A session with **no channel at all** (`test`, `preview`, `imported`) is
nobody's and is never narrowed.

## What a session type supersedes

`SessionManager.create_session` asks the strategy what the new session
supersedes (`SessionTypeStrategy.discard_superseded`) **before** it
writes the new row. The clean-up is preventive, not lazy: nothing is left
marked for somebody to collect later, so there is no state a reader has
to filter.

Only `preview` supersedes anything. A preview is not a conversation
anybody keeps — it exists so somebody can try an app out, and the moment
they try another the previous attempt is of no interest. No listing
returns one, so nothing on screen can lead back to it, and there is
exactly one per person **across every project at once**: the query that
enforces it (`Db.delete_sessions_by_username_and_type`) is keyed on the
username and the type, with no project in it. Both halves go — the rows
(the session, its messages, its tracking) and the ephemeral env the
session was carrying (`EphemeralEnvRegistry`).

`live` inherits the base `discard_superseded`, which supersedes nothing: a
live conversation is what a sessions panel lists and what a benchmark
reads. `backend/tests/test_preview_sessions_supersede.py` holds both that
and the preview case.

`test` overrides `discard_superseded` too, but not to delete a row: a test
session is still what an editor's own sessions panel lists, so the row
stays. What it clears is the *drive* a previous test session of that same
project — the person's own, other test sessions and other projects
untouched — wrote through `Drive.session_id`
(`SessionManager.clear_drive_of_type`, `db/drive.py`'s
`delete_drive_files_for_sessions_of_type`), publishing `output.drive` once
if anything was actually cleared. A fresh test run always starts against
an empty drive, without losing the transcript of the run that used it.

## Who is told what

An **answer** goes back to the connection that asked: the request carried
its id (`origin_id`), and whoever answers sends the frame there.

An **announcement** — a session closed from elsewhere, a conversation
handed to a person — has no request behind it and no connection to answer
to. It goes to whoever is *watching* that conversation: a connection
starts watching when it is told which session it entered, and stops on
`session.exit` — or on `session.ended`, which is the last thing there is
to say about that session.

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
`session.taken_over`, `ui.progress`) + `human_prompt`.

A type outside that list is refused: registering would otherwise be a way
to read an internal type. The registration lives on the connection and
has to be declared again on every reconnection.

Registering is **how a connection says what it is**: `human_prompt` goes
to whoever asked for it and to nobody else, and that is what makes one
tab the one answering as a person. A prompt already waiting when a
connection registers is delivered to it then.

## Contribution points

Not messages: somebody asks, synchronously, and whoever registered fills
in their part. Who fills each one is a build's decision, so it is not
written here.

| Point | What is assembled | Asked by |
| --- | --- | --- |
| `api.state` | the payload of `GET /api/core/state` | `system/api_state_controller.py` |
| `config.services` | the public snapshot of the services | `config.py` |
| `http.controllers` | the controllers to mount | `controller.py` |
| `core.services` | the assembled core | `main.py` |
| `automaton.loader` | which loader answers "give me this automaton". Nobody claiming it is itself an answer — see `project/archive/loader_choice.py` | `main.py` |
| `project.published` | the report a publish answers with, once whoever can turn a revision into a package has added what it made of this one | the publishing service |
| `turn.spoken_reply` | `SpokenReply` — `want()` from whoever runs the interface, `ask()` from whoever can speak | `tracking/tracking_processor.py` |
| `session.services` | `SessionServices` — `offers(name, installed)`: what each service can do for **one** conversation. The session's own project can only narrow the server's switch | `turn/turn_service.py` |
| `trigger.namespaces` | `TriggerNamespaces` — `declare(namespace)`: one more root name a `trigger:` may reference, with what it checks at build time, what it resolves to at run time and what the editor lists for it (`automaton/trigger_namespaces.py`) | `automaton/automaton_builder.py`, `tracking/evaluation_scope.py`, `project/inspector.py` |
| `turn.translatable_labels` | `TranslatableLabels` — `contribute(key, text)`: one more label, not an `Action.ui_button`, that the turn's own translation call should also cover; answered with `{state_key, session_id}` in hand. The result comes back as `turn.translation`, one message per label | `turn/turn_service.py` (the current `choice` env key options), asked by `tracking/tracking_processor.py` |

## Queued work

**`test`, `preview` and `imported` do not belong in the core.** They are
session types, and the four `SessionTypeStrategy` classes plus the
`_STRATEGIES` dictionary that registers them by hand are in
`turn/sessions/session_type_strategy.py`, with `TurnService` naming them
one by one. They should be contributions, leaving the core with `live`
alone — a build without the skills that own them should not know those
types exist. `session.enter {session_type}` does not make this harder:
the type is already a string the registry validates.

**A `preview` session should destroy itself when it closes.** Today the
client does it, so a client that dies first leaves the session behind.
`test` sessions must **not**: they are listed, and the benchmark reads
every type.
