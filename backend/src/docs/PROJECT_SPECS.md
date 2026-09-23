# Project format specification (`index.yml`)

Authoritative, exhaustive, self-contained reference for the `index.yml`
"project" format that drives the Avance state engine — enough to build a
valid project with no other context, by hand or programmatically (e.g. an
LLM generating one). Every rule below is enforced when the project is
validated.

A project is one YAML file, `index.yml`, plus zero or more attachment
files it references by name (§6).

## 0. Chat transport

**The WebSocket is the one and only transport for chat, and every future
chat feature is built on it.** There is no HTTP or SSE fallback, and no
alternative endpoint: a user message travels as an `input.text` frame on
the single `/api/core/bus` connection a page holds, and the reply's
own `output.text_stream`, `output.tool`, `output.text` and `output.error` frames
come back on the same socket, each carrying the `stream_id` the client
minted — the only correlation there is. Everything else (manual actions,
session bootstrap, history, project management) stays plain HTTP.

That socket is also the Bus's reach into the browser, and it behaves
like the Bus: a client is sent only the event types it registered for,
with a `subscribe` frame naming them and an `unsubscribe` frame dropping
them again (see `docs/BUS.md`). Being connected is not being subscribed.

The order of a conversation is the order of the frames on that socket:
the server reads them one at a time and persists each user message right
there, in reading order, before any processing starts. Parallel HTTP
requests could never guarantee that, which is why the chat moved onto the
socket.

## 0.1 One request, one reply

**There is no turn.** A person writes whenever they want, and every request
is owed a reply. What varies is how many requests one reply covers. People
write the way they speak ("hi" / "I have a problem" / "with flight VY3003",
in three sends), and the input stays open while the model is answering, so
several requests routinely pile up.

`input.text` is the request. It is accepted the moment it arrives, in
arrival order — that is what fixes the order of the conversation, and it is
the one thing that may not wait. Answering is not: while a reply is being
written, the requests that arrive are kept, and the next reply is written
for all of them at once. The coalescer is `turn/input_listener.py`
(`TurnInput._requests`, one `_Requests` per session), and it is **in
memory**: what is still waiting when the process dies dies with it.

Accepted is not persisted. A request waits in the session's `Inbox`
(`turn/turn_transaction.py`), with its arrival time, and reaches the DB
together with the reply that answers it — one atomic commit at the end of
the exchange (`turn/atomic_turn_transaction.py`), which is also when the
transition the exchange decided and the env it wrote land. A reply that
fails leaves nothing behind: the DB never holds a user message without the
reply that answered it. Until then, whoever reads the transcript
(`session.messages`, the history route) sees the request all the same,
served from the Inbox with no id yet.

Only **consecutive** `input.text` requests merge. Anything else — a choice
taken (`input.button`), a conversation opened — is a single thing done,
answered on its own and never merged with a text. Three messages sent in a
breath while a reply is being written are two exchanges, not three.

Merged requests reach the model as a **single user message of several text
blocks** — never concatenated into one string, never as several user
messages: Anthropic gets several `text` blocks in one message, OpenAI
several `text` content parts, Gemini several `Part`s in one `Content`. A
reply covering one request sends a plain string.

Consequences worth stating plainly:

- **Signals and triggers are evaluated once per reply**, over the whole
  batch, never once per request.
- **Everything that binds to "the user's message" binds to the last request
  of the batch** — the most recent thing the person is looking at: the
  Tracking row, the bot's reaction, the input tokens. The reply's own frames
  are addressed the way that request was, too.
- **Which requests a reply covered is recorded after the fact.** Each one
  gets `Message.answered_by` set to the reply that answered it
  (`Db.mark_messages_answered`), and that — not adjacency, not stored ids —
  is what `Db.get_turn_history` groups on when it rebuilds the conversation
  for the model. Ids alone no longer say it: a request accepted while the
  previous reply was being written is stored before that reply.
- **Unanswered is a temporary state.** `answered_by` is NULL from the moment
  a request is accepted until the reply covering it is written, and no
  longer: every request is owed a reply. A NULL group sorts last
  (`_turn_key`), which is what puts the requests being answered right now at
  the end of the conversation the model reads.
- **The history budget cuts whole groups.** A group it can only fit part of
  is dropped entirely, rather than shown to the model as an exchange missing
  its own opening.

Elsewhere in this document, **"turn" is shorthand for one reply**
("evaluated each turn", "this turn's own system prompt"). It names no unit
of its own.

## 1. Top-level fields

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `avance-version` | no | string | — | Informational only, never read/validated. Conventionally the first line. |
| `init-action` | **yes** | mapping | — | Where the conversation starts. §5. |
| `states` | **yes** | mapping (name → state) | — | Every state. §4. Must include `init-action.target`. |
| `signals` | no | mapping (name → signal) | `{}` | Numeric values the model estimates each turn. §3. |
| `general-prompt` | no | string | `""` | Appended to a state's `contextual-prompt` for a normal reply. Never sent to a `task.prompt(...)` call (§5.4), which is fully isolated. |
| `attachments` | no | list of filenames | `[]` | Global attachments, sent with every call that also sends `general-prompt`. §6. |
| `env` | no | mapping (name → fields) | `{}` | Declares every `env.<name>` a trigger/env expression may reference. Whether/how the model sees or sets a given key is decided per state, by that state's own `input`/`output` (§4.3) — never a property of the key itself. An action's `env:` (§5.3) can only update a key declared here. |
| `sources` | no | mapping (name → fields) | `{}` | Declares every `source.<name>` a trigger/env expression may reference, and the model may read/write as a tool. §5.2. |
| `project` | no | mapping | — | Identity/display metadata + auto-tracking mode. §1.1. |

Any other top-level key is a build error, with one exception: a
top-level `actions:` is read by nothing and allowed, because a project
can keep its actions there as YAML anchors and merge them into states
(`<<: *action`).

### 1.1 `project:`

```yaml
project:
  id: my_project
  family: com.example.suite
  revision: 3
  ui-label: My Project
  ui-description: A friendly description.
  signal-tracking-on-ai-message: false
  new-session-strategy: resume
  services:
    <service>: required
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | **yes** | string, valid Python identifier | — | This project's own globally unique identity, and the token another project names it by (§5.2). Must satisfy `str.isidentifier()` — letters/digits/underscore, not starting with a digit; no dots/hyphens/spaces. |
| `family` | no | string, free-form | `None` | Visibility scope between projects — never parsed/validated for format. Two projects can observe each other only if they declare the **exact same** `family` string; what one reads of the other is a namespace an installed feature declares (§5.2). Unset means neither observes nor is observed by anything, including itself. |
| `revision` | no | non-negative integer | `0` | This project's own revision number, auto-stamped on every publish — don't hand-edit it going in. |
| `ui-label` | no | string | — | The only "name" ever shown to a user; `id` is never displayed. |
| `ui-description` | no | string | — | Shown in the frontend. |
| `signal-tracking-on-ai-message` | no | boolean | `false` | `false`: auto-tracking runs after the user's message, before the reply. `true`: runs after the reply instead (may reuse model-reported inline values, §3.2). Concerns `ai` states only — a `system` state (§4.1) runs no turn. |
| `new-session-strategy` | no | `resume` \| `restart` | `resume` | What a **new live session** of a returning user inherits. `resume`: it opens in the state the previous session left, every env key and the model's own `global` memory (§5.3) intact, and nothing fires — unless the automaton has never run for that user in a live session, which is the automaton starting and fires `init-action` (§7). `restart`: it opens in `init-action.target` with the env keys and the `global` memory wiped — the declared defaults and `init-action`'s own `env:` apply afresh, and its `task` fires again (§7). Either way, a `local`-scope memory never carries over to a new session regardless: it's per-session by definition (§4). Test and preview sessions always start from `init-action`, whatever this says. |
| `services` | no | mapping (service name → level) | `{}` | What this project asks of each platform service it can reach. §1.2. |

### 1.2 `project.services:`

Every service this installation can offer a project is declared at one of
three levels. A service this mapping never names is `optional`. Which
services exist, and what each one is called, is what this installation
was built with — Settings > Manage services lists them, and each one
states its own name at the end of this document.

| Level | Build | Run time |
| --- | --- | --- |
| `required` | The build must include it; the Build view ticks it and refuses to untick it. | Used whenever it is there. |
| `optional` (default) | Free choice. | Used if the build has it, done without if not. |
| `disabled` | Defaults to left out, still includable. | Never used, even in a build that has it: a call into it comes back exactly as it does when nothing in the build is listening. |

A service a project uses through a `task.<name>(...)` call is `required`
for a build whether or not it is written down here — declaring it
`disabled` and calling it anyway is reported in the Build view, and the
call bounces at run time.

Naming a service this backend does not have installed is not an error: a
project is authored once and built against many backends.

## 2. Names, identifiers, and reserved words

- **State keys** — arbitrary non-empty strings, case-sensitive, matched
  literally by `target:`. `""` is reserved for the engine's implicit
  bootstrap state.
- **Action `name`** — required; unique *within its own state* (`move()`
  returns the first match there).
- **Signal names** — must be valid identifiers (letters/digits/underscore,
  not starting with a digit): referenced as `signal.<name>`, parsed like a
  Python attribute. A non-identifier name builds but can never be
  referenced by a trigger.
- **Reserved names** — a signal can't be named after a core metric
  (rejected at build time):

  ```text
  engagement
  retention
  activity_consistency
  state_stability
  signal_stability
  ```

  These are the engine's own domain-agnostic metrics, computed from
  stored session history (never the model). Unlike a signal
  (`signal.<name>`), a metric is referenced **bare**, interchangeably with
  namespaced values in the same expression (§5.2).

## 3. `signals:`

```yaml
signals:
  mood:
    ui-label: "Mood"
    ui-description: >
      How positive the user's tone sounds in their most recent messages.
    definition: |
      Evaluate the user's tone in their most recent messages on a scale
      from 0 to 100, where 0 is clearly negative/frustrated and 100 is
      clearly positive/enthusiastic. Respond with a single integer.
    attachments: []
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `definition` | **yes** | string | — | Instruction sent to the model when computing this signal (§3.1). Free-form; convention is integer 0–100 or binary 0/100. |
| `ui-label` | no | string | the signal's name | Shown in the frontend. |
| `ui-description` | no | string | `definition` | Shown in the frontend. |
| `attachments` | no | list of filenames | `[]` | Sent with any turn that requests this signal's value (§3.1, §6) — never a call of its own. |

**3.1 Computation.** Signals are requested inline, as part of the same
structured reply a normal chat turn already produces — there is no
separate model call for them. Which signals a turn requests is the
current state's `signal-tracking-strategy` (§4): with `relevant`, only the ones
its own actions read (a state with no such actions asks for none); with
`all`, every declared signal. When it requests any, the system prompt lists
every requested signal's `name`+`definition`, and that signal's own
`attachments` (deduplicated against global/state attachments already
being sent, §6) ride along with the very same turn. The model's reply
includes one JSON object mapping name → value for whatever was requested.
A value that fails to parse (or a failed call) leaves that signal `None`
for this pass — a runtime concern, never build-time. `signal-tracking-on-ai-message`
(§1.1) only controls whether this request happens before or after the
user-facing reply is generated, not whether it is a separate call.

**3.2 Inline reporting.** A model reply can self-report signal values via a
reserved `<avance>...</avance>` JSON tag — a prompting convention, not an
`index.yml` field. When `signal-tracking-on-ai-message` is on,
auto-tracking prefers these values over a fresh computation call.

## 4. `states:`

```yaml
states:
  engaged:
    ui-label: Engaged
    ui-description: >
      The user is actively chatting.
    input-processor: ai
    contextual-prompt: |
      Continue the conversation naturally.
    chat-enabled: true
    history-cutoff: false
    transition-log-level: WARNING
    signal-tracking-strategy: relevant
    ai-memory-scope: global
    attachments: []
    actions: [ ... ]   # see §5
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `input-processor` | **yes** | `ai` \| `system` | — | Who answers in this state — §4.1. `ai`: the model. `system`: the automaton's own scripts, through `chat.write` (§5.3bis); no model call is made for this state. |
| `contextual-prompt` | for `ai` | string | — | System-prompt text, combined with `general-prompt`. Required when `input-processor` is `ai`; ignored when it is `system`. |
| `ui-label` | no | string | the state's key | Shown in the frontend. |
| `ui-description` | no | string | `None` | Shown in the frontend; omitted entirely when absent. |
| `actions` | no | list of actions | `[]` | Outgoing actions — §5. **No actions ⇒ automatically `final`** (derived, never declared). A turn that ends in a final state closes the session once its frames are out (`session.ended`, reason `final-state`): nothing more is accepted on it. A state to stay and chat in — a one-state project's — declares a self-loop action with `trigger: "True"`, which makes it not final. |
| `chat-enabled` | no | boolean | `true` | `false`: a chat message here is rejected outright — only `actions` can proceed the conversation, and the chat shows no text input line. Independent of `final`. Always `false` in a `system` state, whatever is declared. |
| `history-cutoff` | no | boolean | `false` | `true`: excludes every message from before the most recent transition into this state, both from the model's view and from auto-tracking. Combines (doesn't replace) the server-wide token-budget cutoff in `.config.yml`. |
| `transition-log-level` | no | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `"WARNING"` | Log level when a transition **lands on** this state (property of the destination). Operational only. |
| `signal-tracking-strategy` | no | `relevant` \| `all` | `relevant` | Which signals a turn in this state computes (§3.1). `relevant`: only the ones this state's own actions read — in a `trigger`, an `env:` expression or an `on-exit` assignment. `all`: every declared signal, whether or not anything here reads it — for a state whose signals feed a later state, a metric, or a report rather than its own triggers. |
| `ai-memory-scope` | no | `none` \| `local` \| `global` | `none` | Which memory a turn in this state reads and writes (§5.3) — a property of *this* state, not of the transition landing on it. `none`: the memory channel isn't even offered to the model — nothing shown, nothing parsed back, nothing kept. `global`: the shared, project+user-persistent store every session of that pair sees, unaffected by which states came before it. `local`: a fresh, empty memory that starts the moment a transition lands here (a self-loop counts as landing again, as for `history-cutoff`) and is destroyed the moment the session leaves this state — isolated from `global`, which a `local` visit never reads from or writes into. The automaton's `env:` keys are untouched by any of the three. |
| `attachments` | no | list of filenames | `[]` | Sent with every normal reply this state is "current" for. Not sent to a `task.prompt(...)` call (§5.4), which is fully isolated. |
| `ai-may-read-sources` | no | list of source names | `[]` | Sources whose `select_rows_*` reads the model may call, at its own discretion, while replying in this state — §4.2. |
| `ai-must-read-sources` | no | list of source names | `[]` | Same, but the read is forced once per entry into this state — §4.2. A source name can appear in at most one of the two read fields. |
| `input` | no | list of `env:` key names | `[]` | Env keys read into this turn's own system prompt, as a read-only "Current environment" block — §4.3. |
| `output` | no | list of `env:` key names | `[]` | Env keys the model may set this turn, through its own structured reply — merged onto the automaton's env once the turn completes — §4.3. |

**4.1 `input-processor`.** Every state says who answers in it.

`ai` is the model: everything else in this section applies as written.

`system` is the automaton alone. No turn runs in such a state: no model
call, no auto-tracking, no signals — `signal.*` is not defined in any of
its scripts (`trigger`, `env`, `on-exit`, `task`), and a build refuses a
reference to it. A chat message is refused as in any `chat-enabled:
false` state; what moves the conversation on is a manual action or a
choice (§5.2, `choice.<key>`), whose triggers are evaluated when the
button is pressed. The reply of the state is what the `on-exit` of the
action that reached it wrote with `chat.write(body_md)` (§5.3bis) — one
paragraph per call, in order. That text is saved as an assistant message
and published like any reply; when no call was made there is no message
at all — nothing saved, nothing published, the buttons alone. The init-action's `on-exit` writes
the opening message of a `system` initial state the same way.

`contextual-prompt`, `attachments`, `input`, `output`, `ai-memory-scope`,
`ai-may-read-sources`, `ai-must-read-sources`, `reactions-enabled` and
`signal-tracking-strategy` mean nothing in a `system` state and are
ignored, not refused, so a state can be switched between the two without
the file ceasing to build. `signal-tracking-on-ai-message` (§1.2) is an
`ai` matter too.

A trigger fired from an `ai` state that lands on a `system` state does
not regenerate the reply there: with `signal-tracking-on-ai-message:
false` the reply is what the on-exit wrote; with `true` the model has
already answered, and what was written follows as a paragraph of its own.

The former `fixed-message` field is gone: a state that only ever said one
thing is `input-processor: system` whose incoming actions
`chat.write(...)` it. The modernizer (§8.2) does not convert one — where
the text goes is a choice — and refuses the field naming the replacement.

**4.2 Native tool-calling.** `ai-may-read-sources`/`ai-must-read-sources`
each list names from this project's own top-level `sources:` (§5.2) —
always read-only — the model may use, mid-turn, as native tools while
replying in this state — one tool per (source, method): a source named
`flight_records` becomes one callable per read method its driver
supports (`source_flight_records_select_rows_containing`,
`source_flight_records_select_rows_where`,
`source_flight_records_select_rows_in_range`). The two fields differ
only in how much the model is trusted to decide for itself:

- **`ai-may-read-sources`** — the model sees the source's reads and
  decides for itself whether/when to call one, same as any other
  tool-calling setup.
- **`ai-must-read-sources`** — the same reads, but **forced once per entry
  into this state**: on the very first tool-call round of the first turn
  generated since this state was last entered (including the project's
  own bootstrap into its initial state, and a self-loop action re-entering
  the same state), the model is restricted to calling one of *these*
  reads — it cannot just answer instead. From the second round of that
  same turn onward, and every turn after the first, it's `auto` again with
  the full catalog (both fields' tools together). Whether this is "the
  first turn since entering the state" is decided by the backend, from
  the session's own transition/message history — **never left to the
  model to decide, and never re-askable by prompting alone.** Its purpose
  is to make the model *observe* the source's current values before it
  answers.

A source named in either field must declare its own `ai-definition`
(§5.2) — a build error otherwise, the same requirement a signal's own
`definition` gets. The same source name can't appear in both fields for
one state.

Neither field has a project-wide default, deliberately: a tool catalog
costs real tokens on **every** turn in that state, whether or not the
model ends up calling anything — each tool's own JSON schema plus the
provider's own function-calling overhead, on the order of ~1,000 tokens
per turn for three declared sources. Declaring the catalog per state
also documents *where* the model is allowed to look, not just that it's
allowed to — a state with neither field sends the exact same request a
turn always did, before tool-calling existed at all.

Every tool takes the same arguments, whatever the driver:
`select_rows_containing` takes `values` (an array of strings — the row
filter, possibly empty); `select_rows_where` takes `column`,
`operator` (`=`, `!=`, `>`, `>=`, `<`, `<=`), `value`, and optionally
`strings` (an array of strings, same semantics as `select_rows_containing`'s
own `values`, further narrowing the match); `select_rows_in_range` takes
`column`, `start`, `end`, and the same optional `strings`.
Every read returns whole rows — there is no column projection in the
model's own interface. A driver may *narrow* one of those schemas for the
model (`SourceDriver.parameter_schema`) — never changes their shape; no
driver does today.

**4.3 Model-visible env: `input`/`output`.** Whether the model reads or
sets a given top-level `env:` key (§5.3) is decided entirely by *this
state's own* `input`/`output` lists — not a property of the key itself,
and independent of `ai-may-read-sources`/`ai-must-read-sources` above
(those gate `sources:`, never `env:`).

- **`input`** — read-only. A state with a non-empty `input` gets its
  system prompt's own "Current environment" block appended, one
  `key: value` line per name in `input` (declaration order, each value
  truncated to 200 characters) with the key's `ai-definition` indented
  beneath it, placed last so its per-turn changes never
  invalidate the cacheable prefix. A state with an empty `input` gets no
  block at all — not even empty. There is no model-facing write path
  through this block: the model is told to change these only through
  `output` below (or wait for an action's own `env:` script), never by
  restating them in its `memory` field — a *declared* key named there is
  discarded outright.
- **`output`** — the model's own structured reply carries a separate
  `output` field, one value per name in this state's `output`; each name
  is described to the model from its own env key's `ai-definition` (§5.3)
  and asked for in its own env key's `type` — a `string` is a string (a
  Markdown text included), a `number` a number, a `bool` a boolean, or
  null when this turn gives none, which leaves the key unset.
  Once the turn completes, every reported name that's actually in this
  state's `output` is copied onto the real env key — anything else the
  model reports under `output` is ignored. That copy lands *before* the
  action this turn fires runs its own `env:`/`on-exit` (§5.3, §5.3bis):
  the model proposes, the script decides — an `on-exit` assignment to a
  key the model also reported is the value that stays.
  Requested *before* the reply text only when this state has a
  triggerable action (§5.1) *and* `signal-tracking-on-ai-message: false`
  — the one case where a trigger needs this turn's own output values
  before the reply commits, so a fired transition can discard and
  regenerate the reply instead of showing one already contradicted by its
  own state change (§5.3's "Optimistic reply and writes"). Every other
  state gets its `output` requested *after* the reply text, so a field
  meant to echo or summarize what the reply just said actually can —
  asking for it first would have the model commit to a value before it
  has composed the text that value is supposed to reflect.

An `input`/`output` name must be declared in the project's own `env:`
section, and that env key must declare its own `ai-definition` — a build
error otherwise, the same requirement `ai-may-read-sources`/
`ai-must-read-sources` place on a source's own `ai-definition`.

## 5. `actions:` (nested under a state)

```yaml
actions:
  - name: advance
    ui-label: "User is ready to move on"
    ui-button: "Move on"
    target: next_state          # omit for a self-loop (stays on this state)
    trigger: "signal.mood >= 70 and engagement >= 20"
    task: |
      line = task.prompt('Write a short celebratory one-liner.')
    on-exit: chat.celebrate()
    env:
      reset_counter: True
      number_of_steps: env.number_of_steps + 1
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `name` | **yes** | string | — | This action's own identifier — what a manual firing references. |
| `target` | no | string | this action's own state | Destination state; must be a real key (or the current state itself). Omitted/self-referential ⇒ self-loop: only the action's own effects happen. Fired manually (a button, a choice) in an `ai` state, nobody answers — no model call, no message, since nothing new was said; in a `system` state the reply is what its `on-exit` wrote (§4.1), as for any action reaching that state. |
| `trigger` | no | string (expression) | `None` | Boolean expression over signal/metric names — §5.2. Absent ⇒ manual-only (never auto-fired). |
| `task` | no | string | `None` | One or more `task.<name>(...)` calls, one per line — side effect of firing, run in the background off the request (§5.4). Per-action, not per-destination-state: two actions landing on the same state can each carry a different (or no) value. |
| `on-exit` | no | string | `None` | One or more `env.<key> = expression` lines, `name = expression` locals and/or bare `chat.<method>(...)` calls, one per line — same timing as `env:` (and its future replacement for the write half), run synchronously, in this same request. §5.3bis. |
| `env` | no | mapping key → expression | `None` | Updates the project's environment memory when this action fires. §5.3. Legacy — new actions should write the same updates as `on-exit` lines instead. |
| `ui-label` | no | string | `name` | Shown in the frontend. |
| `ui-button` | no | string | `ui-label`, then `name` | Manual-action button text. |
| `ui-description` | no | string | `None` | Shown in the frontend. |

An action has no `attachments:` of its own — list attachments on the
destination state's or the top-level `attachments:` instead (§6).

Any other field is a build error (§8.1). Several of them were renamed
rather than invented — `actuator` and then `on-enter` are both `task`,
`action-prompt` is a `task.prompt(...)` call inside `task`, and the
`actuator.*` namespace all three used to call split into `task.*` for
what reaches a service and `chat.*` in `on-exit` for what reaches the
conversation. A build knows none of that; what rewrites them before it
ever sees the file is §8.2.

**5.1 Manual vs. triggered.** Any action can be fired manually, by name —
its trigger (if any) is never evaluated for a manual firing. Actions
**with** a `trigger` are also evaluated by auto-tracking after every
signal computation, in **YAML declaration order** — first `true` wins,
FIFO, remaining ones skipped that turn.

Leave an action manual-only (no `trigger`, no signal needed) when it's a
**deterministic** user choice rather than something inferred from what
they said — simpler on every axis (fewer signals to estimate, fewer
triggers to evaluate, and a UI button instead of something the system
might read wrong). Reserve `trigger` for transitions that genuinely
depend on interpreting the conversation.

**5.2 Trigger expressions.** A boolean-ish expression evaluated with
[`simpleeval`](https://pypi.org/project/simpleeval/) — comparisons,
boolean logic, arithmetic, `len(...)`, plus attribute access/calls
**only** on the namespaces below (never arbitrary Python — no imports,
no other bare calls):

```text
signal.mood >= 70
engagement >= 20 and retention >= 1
(signal.mood >= 40 and engagement >= 10) or signal_stability < 20
session.number_of_user_sessions() >= 3 and session.state_duration_in_minutes() > 30
user.role == "admin"
len(env.notes) > 0
```

| Namespace | Resolves to | Access |
| --- | --- | --- |
| `signal.<name>` | A declared signal | attribute (value, or `None` before first computation) |
| `env.<name>` | A key declared in top-level `env:` (§5.3) — never a model-reported free-form value | attribute |
| `session.<name>` | Engine fact about the current user+project session (`current_session_duration_in_minutes`, `last_user_session_datetime`, `number_of_user_sessions`, `state_duration_in_minutes`) | **call**, e.g. `session.number_of_user_sessions()` |
| `user.<name>` | Current user's account field (`email`, `name`, `picture_url`, `provider`, `provider_user_id`, `created_at`, `last_login`, `active_project`, `role`) | attribute |
| `source.<name>.<method>(...)` | A source declared in top-level `sources:` — below | method call, e.g. `.select_rows_containing(...)` |
| `datetime.<name>` | Python's `datetime`/`timedelta`/`timezone` only, mainly for `task.defer`'s `when` | call, e.g. `datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)` |
| `choice.<key>` | `<key>` an env key declared of type `list` (§5.3): the option just pressed, for the one trigger evaluation the press starts — `""` in every other evaluation and under every other `list` key. Exactly `choice.<key>`, in `trigger:`, `env:` and `on-exit:` — never in `task:`, which runs later, against a scope of its own | attribute |

A **bare** name is only ever a core metric (§2) — nothing else may appear
unnamespaced. An installed feature may declare one more namespace of its
own for `trigger:` (its own section of this document says which, and what
it holds); a build without that feature refuses the reference as an
undefined name. `task.<name>(...)` is reserved but only valid inside
`task:` (§5.4); `chat.<name>(...)` is reserved but only valid inside
`on-exit:` (§5.3bis) — neither is available in `trigger:`/`env:`, and
each is off-limits to the other's own script.

**Data sources.** A project declares its own named sources under a
top-level `sources:` mapping — each one a handle a trigger/env
expression addresses as `source.<name>.<method>(...)`:

```yaml
sources:
  pino:
    ui-label: Flight records
    ui-description: This app's own flight-schedule CSV.
    ai-definition: |
      One row per flight, columns: flight code, date, delay reason.
      Search by flight code alone to get every date it ever flew — add
      the date too to narrow down to one row.
    url: avance:behaviour/flights.csv
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `url` | no | string, `<scheme>:<path>` | `""` (unconfigured) | Which driver resolves this source, and that driver's own target. Left unset, the source builds fine but none of its methods can be called yet — an "undefined name(s)" error, same as an undeclared source. |
| `ui-label` | no | string | this source's own key | Shown in the frontend. |
| `ui-description` | no | string | `None` | Shown in the frontend — **never sent to the model.** |
| `ai-definition` | conditionally | string | `None` | Written *for the model*: what this file contains and how to search it well. Becomes part of the tool's own description whenever this source is exposed as a native tool. **Required** (build error otherwise) for any source named in some state's own `ai-may-read-sources`/`ai-must-read-sources` (§4.2) — same requirement a signal's own `definition` gets; optional otherwise. |

`ui-description` and `ai-definition` serve two different readers, and the
distinction is load-bearing, not stylistic: `ui-description` is UI text —
it never reaches the model, the same way an action's own `ui-description`
doesn't. `ai-definition` is the only place a project author gets to tell
the model what a source's raw file actually contains and how to query it
well (e.g. "search by flight code *and* date, or you'll get every date
that flight ever flew") — write it with the model as the reader, not a
human skimming the Inspector.

Every source — whatever its driver — implements the same uniform
interface, and is bounded by construction: a result over
`MAX_SOURCE_RESULT_CHARS` is refused outright with an `error: response too
long — try again by providing more specific filterso.` — never silently
truncated, so the model always knows a result it gets is the complete
match set. A given driver only
ever implements the methods that make sense for it (its
`SUPPORTED_METHODS`); calling one it doesn't — in a script or through a
state's tool fields — is rejected at build time the same way an
undeclared source is. The driver also decides, on its own, which of
those the model gets as tools (its `TOOL_METHODS`): a script sees every
supported method, the model only the listed ones. There is no hierarchy
of source kinds: method support is the whole compatibility story.

- `select_rows_containing(*values)` — grep-like lookup: the header row
  plus every **whole** row containing **every** given value
  (case-insensitive substring match, AND'd). No row at all satisfying the
  filter returns `""` — not even the header — so
  `source.<name>.select_rows_containing(...) != ''` is a real existence
  check ("the model proposes, the script verifies," below).
  `source.pino.select_rows_containing('VY3003', '2026-08-16')` finds the
  one row for that flight on that date.
- `select_rows_where(column, operator, value, *strings)` — the header row
  plus every whole row whose `column` satisfies the comparison, further
  narrowed by `*strings` with the very same AND'd, case-insensitive
  substring semantics as `select_rows_containing`.
  Operators: `=`, `!=`, `>`, `>=`, `<`, `<=`. `value` may be a bare
  number as well as a string. Both sides are compared as
  numbers when both parse as numbers, as moments in time when both parse
  as ISO dates/datetimes (`YYYY-MM-DD`, `YYYY-MM-DD HH:MM`), and
  case-insensitively as text otherwise — so
  `source.pino.select_rows_where('data_partenza', '>=', '2026-08-16', 'Barcelona')`
  reads as a date comparison AND'd with a row-text filter, not a string one.
- `select_rows_in_range(column, start, end, *strings)` — the same
  whole rows, for a `column` between `start` and `end`, **both included**
  (numbers and ISO dates alike), further narrowed by `*strings` the same
  way:
  `source.pino.select_rows_in_range('data_partenza', '2026-08-01', '2026-08-31', 'Barcelona')`.
- `value(*values, key)` — the `key` cell of the *first* row satisfying
  the same filter as `select_rows_containing`, as a single scalar — a
  number (`int` or `float`) when the cell reads as one, the raw string
  otherwise; `""` if no row matches, an error *text* if `key` isn't a
  real column.
  Scripts and trigger/env: expressions only — never exposed to the model,
  which reads through the `select_rows_*` tools instead.
  `source.pino.value('VY3003', key='flight')` reads one field without
  parsing a table.
- `column(column, *values)` — every `column` cell of the rows satisfying
  the same filter as `select_rows_containing`, as a **list** (no values
  at all: the whole column); `[]` if no row matches, if `column` isn't a
  real column, or if the result would exceed the size bound. Scripts and
  trigger/env: expressions only — never exposed to the model.
  `'ABC-1' in source.casos.column('archivo')` is a membership check, and
  `env.casos = source.casos.column('archivo')` keeps the list.
- `select_subtable(*columns)` — the named columns of **every** row, as a
  **dict** `{column: [cells]}` in the order asked:
  `source.casos.select_subtable('caso', 'titulo')` is
  `{'caso': [1, 2], 'titulo': ['Ana', 'Luis']}`; `{}` if the file has
  no row at all, if any column isn't real, or if the result would
  exceed the size bound. Scripts and
  trigger/env: expressions only — never exposed to the model. This is
  the shape `chat.write_table` (§5.3bis) takes.
- `row_where(column, operator, value, *strings)` — the *first* row
  `select_rows_where` would return, as a **dict** (`{column: cell}`, every
  column of the file); `{}` if no row matches, if `column` or `operator`
  isn't real, or if the row would exceed the size bound. Scripts and
  trigger/env: expressions only — never exposed to the model.
  `env.caso = source.casos.row_where('caso', '=', 1)` keeps one whole
  record, and the prompt's env block renders it as JSON.

Every `select_rows_*` read returns whole rows — `select_subtable` is the one
projection — and an unknown column or operator comes back as an error
*text*, never an exception. Every driver implements `select_rows_containing`; the
column-filtered reads and `value` only where they make sense for that
driver (its own `SUPPORTED_METHODS`). Sources are read-only — no driver
writes anything, ever.

Two drivers exist today, under the schemes `avance` and `websearch`:

**`avance:<path>` — an archive file.** Read-only access to one of this
project's own files, addressed by `url`'s own path (exact path or unique
basename under `behaviour/`, resolved directly from storage at the
conversation's own pinned automaton revision, never "whatever's published
now" — not the `attachments:` mechanism, nothing is eagerly loaded).
Assumes a normalized CSV (header + one row per record; the separator is
detected). Implements every `select_rows_*` read, `value`, `column`,
`select_subtable` and `row_where` — nothing writes. Every read goes straight to the project's
own stored file, at the conversation's own pinned revision — the same
content for every session, no per-session copy of anything. A
whole-file read is `attachment.read(name)`'s job (`on-exit`/`task` only), not a
`source.*` capability.

**`websearch:user` — the last web search this user ran.** The same reads
as `avance:` above, over the CSV `task.websearch(...)` (§5.4) last
brought back for whoever is talking now, rather than over a file the
project ships. It is kept in this project's own cache namespace, one
entry per user (`cache/websearch/<project id>/<user id>`), so nothing
is shared between two people in the same project and a new search
replaces the previous one. `user` is the only path the scheme takes —
the scope of the result, not a file name — and any other is a build
error. A user who has never searched reads an empty cache file — `""`,
the same answer a search that matched nothing gives — never an error:
a source is a table to query, and "nobody has searched yet" is a state
the project's own triggers handle as "no rows", not as a failure. In the editor it is declared
from the file explorer's own "+" menu, **Add web search**, next to **Add
source**, and writes no `sources/` archive: its panel shows the editing
user's own cached table read-only — no cell editing, no upload, no new
rows — with a **Clear cache** button that empties it.

The automaton's own `env:` keys are never reached through a `sources:`
driver — the model reads/sets them through a state's own `input`/`output`
instead (§4.3), independent of `sources:` entirely.

New drivers are a code change, not something a project author adds.

Every reference is validated at build time (`signal.<name>` must be
declared, `env.<name>` must be set by some action somewhere,
`source.<name>` must be declared in top-level `sources:` and `<method>`
must be one that source's own driver actually implements,
`session`/`user` names must be from the fixed lists, bare names must be
a recognized metric) — anything else fails with an "undefined name(s)"
or parse error. At evaluation time: a referenced `signal.<name>`
still `None` short-circuits the whole expression to `false`; any other
failure (e.g. an `env.<name>` never actually set) is logged and also
treated as `false` — a trigger can never crash a turn.

A `signal.<name>` matched against a literal no signal value can ever be —
a string, a boolean, or a number outside 0–100 — is rejected at build
time as well. Whatever the model answers is coerced to that domain
before a trigger sees it (§3.1), so `signal.mood > 150` or
`signal.mood == "alto"` has one fixed outcome whatever the turn does.

**5.3 Memory, env, and the model.** Two stores live per user+project,
with two different owners, and the names are load-bearing:

- **memory** — the model's own free-form notes (`key: value`, always
  strings), written only by the model through the `memory` field of its
  structured reply (a delta: only new/changed notes) and read only by the
  model, in the prompt's own "Current memory" block. No script or trigger
  ever sees it; the Inspector's Memory section shows and edits the
  `global` one. Which store a turn's memory field actually reads/writes —
  none at all, the project+user `global` one below, or a fresh per-session
  `local` one — is decided by the current state's own `ai-memory-scope`
  (§4), never a project-wide constant: `global` is what "memory" means
  everywhere below in this section; a `local`-scope state instead reads
  and writes a store that starts empty on entry and is gone on exit,
  never touching `global`.
- **env** — the automaton's declared variables: the project's top-level
  `env:` keys, deterministic, written by an action's own `env:` field
  (below) — or, for a key some state lists in its own `output` (§4.3), by
  the model's own structured reply — and read by triggers, scripts
  (`env.<name>`) and, where a state's own `input` lists it (§4.3), the
  model. `session` facts (§5.2) are never part of either.

```yaml
env:
  reset_counter:
    type: bool
    ai-definition: Whether the counter was just reset.
  number_of_steps:
    type: number
  pnr:
    type: string
    ai-definition: The 6-character record locator the customer gives you; empty until they do.
  slot:
    type: list
    ai-definition: The appointment slots on offer.
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `type` | **yes** | `number` \| `string` \| `bool` \| `list` | — | What the key holds, declared once: a number (`int` or `float`, never a bool), a string, a bool, or a `list` — a list of strings, the options a script writes and the person picks from (see **List keys** below). A key without `type`, or with any other value, fails the build naming the key and the four admitted values. |
| `ai-definition` | conditionally | string | `None` | What this variable means, written *for the model* — and the only description an env key has: the editor shows this one too. **Required** (build error) whenever some state lists this key in its own `input`/`output` (§4.3) — same requirement a source exposed to the model gets; optional otherwise. Becomes that field's own description in the prompt's env block / output schema. |

A key declares what it holds and nothing else: it takes no `value`, and a
`value` field fails the build like any other unknown one. Every key starts
at its type's own default — `0`, `""`, `False`, `[]` — applied once, in
declaration order, the first time a session opens; a key that must start
at anything else is written by the init-action's own `env:` (§7).

An action's `env:` can only update a key declared here, never invent one
(fails build validation otherwise). Declaring a key here doesn't by
itself update it on any turn. Whether the model ever sees or sets a given
key is decided entirely per state, by that state's own `input`/`output`
(§4.3) — never a property of the key itself.

**List keys.** A `list` key holds a list of strings a script writes (an
action's `env:` or `on-exit`) — the options on offer when a trigger
reads it through `choice.<key>`, or any list a script keeps for its own
use. A list never reaches the model: a `list` key in a state's
`input`/`output` fails the build. Its `ai-definition` is read only by
the editor and as an option button's description.
In any state whose actions' `trigger`s read `choice.<key>`, the current
options become buttons, one per option, after the state's own pressable
actions (see BUS.md, `state.buttons`). Pressing one writes nothing: the
option is the value of `choice.<key>` for the single trigger evaluation
the press starts — `""` everywhere else, in every other evaluation and
under every other `list` key — and the first action whose trigger
answers transitions as a manual action does, its own `env:` and
`on-exit` reading the same `choice.<key>`. No trigger answering, nothing
happens. The pattern:

```yaml
env:
  slot:
    type: list
  booked_slot:
    type: string
states:
  pick:
    contextual-prompt: Offer the slots.
    actions:
      - name: offer
        target: pick
        trigger: "env.slot == []"
        env:
          slot: "['morning', 'evening']"
      - name: book
        target: booked
        trigger: "choice.slot != ''"
        env:
          booked_slot: choice.slot
```

**Profiles.** A `list` key may hold, instead of strings, a list of
*profiles*: dictionaries with the three string fields `title`,
`description` and `key`, plus an optional `picture_url`. Nothing else
changes in the automaton — the options are offered the same way, and
`choice.<key>` is the pressed profile's `key`, a string, so the trigger
above reads unchanged. What changes is the presentation: a chat that
receives profiles shows one at a time in a dialog — picture, title,
description and a full-width button labelled with the profile's `key`,
flanked by arrows that move through them — instead of a row of buttons.
The dialog has no way out but choosing: no close button, no Escape, and
the options are nowhere else, so offering profiles is asking a question
the person has to answer. Choosing does not close it — it goes quiet
until the turn it started says how it went, and closes only once the
options are withdrawn, which a transition does. A turn that fails
instead leaves the same options standing, and the dialog asks again. A channel with no dialog shows the titles as
buttons. `picture_url` is
what `media.<doc_id>.url()` returns for an image among the project's
files, or any absolute URL, used as written; a bare file name
(`'ada.png'`) is served from the project's media the way a skin's
`url(...)` is. A profile that carries no `picture_url` is shown without
a picture. A list mixing strings and profiles, or a dictionary missing
a required field or adding one of its own, is not a `list` value and is
discarded like any other value outside its type (below).

```yaml
      - name: offer
        target: pick
        trigger: "env.hero == []"
        env:
          hero: >
            [{'title': 'Ada', 'picture_url': '/ada.png',
              'description': 'The analyst.', 'key': 'ada'},
             {'title': 'Grace', 'picture_url': '/grace.png',
              'description': 'The admiral.', 'key': 'grace'}]
      - name: chosen
        target: play
        trigger: "choice.hero != ''"
        env:
          hero_key: choice.hero
```

**The type is enforced twice.** At build, every expression that writes a
key — an action's `env:` entry, an `on-exit` assignment, the key's own
`value` — is compared with the declared `type` whenever its kind is
statically known (`"42"` into a `string` key fails; `env.other` is not
knowable ahead of a turn and passes). At run time, every value an
action's `env:` or `on-exit` produces is checked against the declared
type before it is written: `number` takes an `int` or `float` and never
a bool, `string` a `str`, `bool` a `bool`, `list` a list whose
elements are all strings or all profiles. A value outside its type is treated exactly
like a key whose expression failed to evaluate — logged with the key,
the declared type and the type found, and discarded, while the action's
other keys are written. Nothing is coerced.

**The prompt's env block.** The model sees the automaton's env only in a
state whose own `input` is non-empty (§4.3); there, the system prompt
ends with a "Current environment" block — one `key: value` line per name
in `input`, the value rendered in full, the key's `ai-definition`
indented beneath it, placed last so its per-turn changes never
invalidate the cacheable prefix. A block too large to fit fails the
turn outright, through the request's own input-token-budget-per-turn
cap, rather than silently handing the model a cut value. Anywhere else the
block does not exist, not even empty. The memory block is a separate
block with its own heading, and the model is told to change variables
only through `output` (§4.3), never in the `memory` field — a *declared*
key named there is discarded outright (`Env.update`'s own `declared_keys`
filter), whether or not that key has been set yet: the model reporting
`pnr` as a memory note before ever reporting it as `output` must not let
it leak into memory instead of being silently dropped. A project that
declares nothing (the Hello world sample) has no tools and no env
block: just the memory.

**The model proposes, the script verifies.** The intended way to use the
binding: give the model `output` keys for the facts it has to collect
from the user (`pnr`, a corrected `flight`), let it report them, and
make the transition a **trigger** that checks those values against the
project's own data —

```yaml
trigger: "source.tickets_sold.select_rows_containing(user.email, env.flight) != '' and env.pnr != ''"
```

— rather than a signal about whether the model *believes* it collected
them. The model proposes the values, the script verifies them, the
transition belongs to the script. (See the "Vueling Refund" sample's
`locate_booking` state.)

**Optimistic reply and writes.** With `signal-tracking-on-ai-message:
false`, the reply is generated first and regenerated if a signal fires a
transition. An `output` value the model reported during the discarded
reply is already persisted when the regeneration starts, is **not**
rolled back, and the regeneration in the new state sees it as the current
value. This is deliberate: the write was the model's decision on the
user's message, not on its own discarded wording.

**Persistence and reset.** Every env key persists per (project, user) —
across sessions, together with the state a live session was left in and
the model's own `global` memory. `project.new-session-strategy: restart`
(§1.1) wipes all three when a new live session opens. Under `resume`, a
case is started afresh by resetting its keys on the action that opens it.
A `local`-scope memory is never part of this: it lives per-session, not
per (project, user), so it never survives past the session that collected
it regardless of `new-session-strategy` — every transition empties it
outright, landing on the same `local` state again included (§4).

**Action `env`.**

Each `env:` entry is `key: expression`, same namespaced scope/mechanics
as `trigger` (§5.2) minus the boolean cast — any simple value (string,
number, bool, `None`, ...):

```yaml
    actions:
      - name: advance
        target: b
        trigger: "signal.mood >= 70"
        env:
          reset_counter: True
          number_of_steps: env.number_of_steps + 1
```

Self-referencing a key this same mapping also writes (`number_of_steps`
above) is common and always valid — it reads that key's last stored value
from *before* this action fired.

Writes only happen as a side effect of the action actually firing
(manual, or the exact moment its `trigger` turns `true`) — never merely
from having a `trigger` that stays `false`. Same build-time validation as
`trigger` (syntax + unknown-name). At evaluation time, a failure (a
recognized-but-never-set `env.<name>`, a runtime error) is logged and
that key's previous value is left untouched — one bad key never blocks
the rest of the mapping. Updates merge onto the store and land **before**
anything else that turn generates a reply (this action's own `task`,
the destination state's opening message, or a normal chat turn) — the
very next prompt already reflects it.

Persisted separately from the model's own memory; only this action-set
store feeds a trigger's `env.<name>`. The action-set one is never
directly editable in the Inspector — only ever a side effect of its
action firing again, or of the model's own `output` report on a key some
state lists (§4.3).

**5.3bis Action `on-exit`.** The future replacement for `env:` above,
plus a second statement shape of its own — one or more statements, one
per non-blank line, split with `task`'s own statement grammar
(`TriggerExpressionAnalyzer.task_statements`: a single call may itself
span several lines, and a `#` comment just works). Each line is
**either**:

- an `env.<key> = expression` assignment — the future replacement for
  the `env:` mapping above: same env-write contract (the key must
  already be declared under top-level `env:`, same "one bad key never
  blocks the rest" evaluation-time failure handling, same "lands before
  anything else that turn generates a reply" timing), same namespaced
  scope/mechanics as `trigger`/`env` (§5.2, minus the boolean cast) —
  unlike a `env:` mapping entry, an accepted write lands in place right
  away: a *later* line's own `env.<key>` read (another assignment's RHS,
  a local, a `chat.<method>(...)` argument) sees the value this line
  just wrote, never the one from before this action fired — on-exit is
  an ordered script, not an unordered mapping; **or**
- a `name = expression` local — `task`'s own assignment shape (§5.4):
  `name` may not shadow a reserved namespace or core metric, may only be
  read by a *later* line, and is dropped once the script ends — it never
  reaches the env, the task that follows, or the next turn; **or**
- a bare `chat.<method>(...)` call — `on-exit`'s own side effect,
  described below.

Unlike `task` (§5.4), no `task.<name>(...)` calls: `task:`'s own
`task.<name>(...)` calls stay off-limits, that remains `task`'s own
job. `attachment.read(name)` (§5.2, data sources) is available in either shape, under
the same build-time checks — a string-literal name, an existing text
file, under the size limit — so an action can store a file in an env
key or show one, e.g. `chat.show(attachment.read('rules.md'))`.

`media.<doc_id>.url()` is available the same way, on-exit only — one
attribute per file uploaded under this project's own `media/` folder,
`doc_id` its basename without extension (a file whose basename doesn't
parse as a Python identifier isn't reachable this way, only by name in
the file explorer). Unlike `attachment.read`, it never reads the file's
own bytes — it returns the same download url the frontend already
fetches every other project file's content from, for `chat.show_media`
(above) to hand to the browser, e.g.
`chat.show_media(media.report.url())`.

```yaml
    actions:
      - name: advance
        target: b
        trigger: "signal.mood >= 70"
        on-exit: |
          steps = env.number_of_steps + 1
          env.reset_counter = True
          env.number_of_steps = steps
          chat.celebrate()
          chat.notify('Nice!', 'You reached **state B** in ' + str(steps) + ' steps.')
```

An action may declare `env:` and `on-exit` at once (only already-published
YAML predating `on-exit` should still have a reason to); should both
write the same key, `on-exit`'s own value wins.

**`chat.*`** is `on-exit`'s own namespace — reachable only here, never
from `trigger:`/`env:`/`task:`. Unlike `task:`'s own script (§5.4),
every `on-exit:` script — its own env writes and its own `chat.*`
calls alike — runs **synchronously, in the same request that fired the
action**, never hibernated as a background job: `chat.*` has no
model/network call of its own to keep off the event-loop thread, so
there's nothing to defer. Ten methods exist:

- `chat.write(body_md)` — the reply of the `system` state (§4.1) this
  action leads to: `body_md` is saved as the assistant's message and
  published as one, exactly like a model's reply; several calls in one
  exchange join as paragraphs. Only available in the `on-exit` of an
  action (or the init-action) whose target declares `input-processor:
  system` — on the way into an `ai` state, a self-loop included, it is
  an undefined name, since the model answers there.
- `chat.write_table(table)` — `chat.write` of a markdown table: `table`
  is a dict `{column name: [cells]}`, one column per key in order, cells
  strings, numbers or booleans — exactly what `source.<name>.select_subtable(...)`
  (§5.2) returns, e.g. `chat.write_table(source.casos.select_subtable('caso', 'titulo'))`
  or `chat.write_table({'Name': ['Alice', 'Bob'], 'Score': [7.5, 4]})`.
  An empty dict writes nothing. A short column is padded with empty
  cells; a `|` or a newline inside a cell is escaped so the table
  survives it. Same availability as `chat.write`: only on the way into
  a `system` state.
- `chat.celebrate()` / `chat.notify(title, body_md)` / `chat.show(body_md)` —
  compile straight to `taskActions.js` locals of the same name
  (confetti / toast / dialog). Nothing runs server-side beyond building
  that JS snippet — the tunnel is exact, e.g. `chat.notify('Nice!', 'Well done')`
  reaches the browser as literal `notify("Nice!", "Well done")`. `show`
  renders `body_md` (markdown) in the app's existing generic dialog
  (DialogHost.vue) rather than a toast — no title, closed via its × button.
  Every `on-exit:` line's own joined snippet text reaches the browser
  over the websocket as a single `ui.notification` frame — the exact same
  frame shape `task:`'s own tunneled calls use (§5.4), just pushed
  inline instead of from a background worker.
- `chat.clear()` — blanks the chat window, so whatever is said next
  opens it as its first message. Purely what is on screen, and a
  `taskActions.js` local like `celebrate`: nothing is deleted and no
  history is cut, so the model still sees the whole conversation and
  reopening or reloading shows the transcript whole again. Cut what the
  model sees with a state's own `history-cutoff` (§4.2) instead — the
  two are independent, and only a state that declares both blanks the
  window and forgets at once.
- `chat.bind_env(env.<key>)` — binds one env key to the chat window's
  look: from then on the window carries the CSS class
  `env-<key>-<value>`, following every later `env.changed` of that key,
  so a skin can style the window by its value. The argument must be
  `env.<key>` itself, a declared key that is not a `list` — both checked
  when the project is built. Several calls bind several keys. A
  `taskActions.js` local like `clear`, carrying the key and its value as
  this script left it.
- `chat.unbind_env_all()` — drops every `chat.bind_env` binding, and the
  classes with them. Moving the window to another conversation drops
  them too.
- `chat.switch_to_human(user_id)` — hands the session to a person:
  `user_id` (their username/email) is pushed a notification with a link
  to take over this session's next turns as the human, in place of the
  AI. No JS of its own reaches the browser.
- `chat.switch_to_ai()` — hands a session back to the AI after
  `switch_to_human`. No JS of its own reaches the browser.
- `chat.show_media(url)` — shows one of this project's own `media/`
  files: `url` is that file's own download url, e.g.
  `media.<doc_id>.url()` (below). Compiles the same way `chat.show` does
  — a tunneled `show_media(url)` JS snippet — but the frontend decides
  what to render from `url`'s own extension: an image, PDF, or Markdown
  file opens in the app's existing generic dialog; an audio file plays
  instead, in a looping background player, no dialog at all. Only takes
  effect in webchat — the one frontend that has a dialog/player to show
  it in.
- `chat.chart(title, *series, max_scale=None)` — one
  `{'line': ..., 'value': ...}` dict per bar, each written as its own
  argument:

  ```
  chat.chart('Score',
    {'line': 'Emotional exhaustion', 'value': env.emotional_exhaustion},
    {'line': 'Depersonalization', 'value': env.depersonalization},
    {'line': 'Overall', 'value': env.score},
    max_scale=100
  )
  ```

  Publishes `output.chart` (`title`, `series`, `max_scale`, the
  session's own `session_id`) on the bus, delivered only to a connection
  actually showing this conversation — like `chat.notify`, no JS of its
  own is tunneled; unlike it, this reaches the browser as a real bus
  message, not a `ui.notification` frame, and renders as a bar chart,
  one bar per `line`.

  `max_scale` is the value a full bar stands for. Without it the chart
  scales to its own values — the smallest bar empty, the largest full —
  which compares the lines against each other and says nothing about how
  high any of them is. A score out of a known maximum needs the maximum:
  `max_scale=100` draws 42 as a bar 42% long, and four low scores as
  four short bars rather than one full one.
- `chat.progress(title, percentage)` — publishes `output.progress`
  (`title`, `percentage`, the session's own `session_id`) on the bus,
  delivered only to a connection actually showing this conversation —
  same delivery as `chat.chart`, a real bus message rather than a
  tunneled JS call. Distinct from the platform's own `ui.progress`
  (a user-wide broadcast bar, unrelated): this one lives inside the
  current turn's own chat bubble. `percentage` below 100 shows a title
  and a bar; `None` (never called) or 100 and above shows the bubble as
  usual, with no bar.

`switch_to_human`/`switch_to_ai` never run for real during a draft/test
conversation unless actuators are explicitly enabled for it — while off
(the default there) `switch_to_human` is suppressed and reported back
as a `notify(...)` toast describing what would have happened instead,
same suppress-and-report contract `task:`'s own real side effects get
(§5.4). `celebrate`/`notify`/`show`/`show_media`/`switch_to_ai`/`chart`/`progress`/`bind_env`/`unbind_env_all`
have no real-world side effect to suppress, so they always run.

**5.4 Action `task`.** One or more statements, one per non-blank
line, same namespaced scope as `trigger`/`env` (§5.2) as a firing side
effect, same timing as `env:` — except it additionally sees `task`
and does **not** see `session.*`/`session.metric.*` (a call may be
deferred past the firing session's own lifetime, so the whole scope is
built without a session rather than allowing it selectively) or
`chat.*` (`on-exit`'s own namespace, §5.3bis — never `task`'s). Each
statement is either a `task.<name>(...)` call, or a simple
`name = <expr>` local-variable assignment — the only other shape
allowed — making `name` usable, bare, by every *later* statement in this
same task script (never an earlier one, never a different action's
own task). This exists to let one `task.prompt(...)` call's
result reach more than one later call without re-running the model each
time:

```yaml
task: |
  translated = task.prompt('Translate to Catalan: The party starts at 9pm.')
  task.defer(lambda: task.prompt(translated), datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
```

**Every task script runs as a task, never inside the request that
fired it.** The transition and the action's `env:` writes are applied
synchronously (they feed the very next prompt); the script itself is
hibernated in the database as a task due immediately and executed by a
background worker — a `task.*` call is a model call or a network call,
and neither belongs in a chat turn's own response time. Whatever the script tunnels reaches the
browser over the websocket as a `ui.notification` frame, a moment after
the turn's own response, never inside it. `task.defer` (below) is the
same task with a later due time.

**A statement that fails does not stop the ones after it.** Each is
logged and skipped, the rest of the script runs, and whatever the
successful ones tunnelled is pushed as usual — but the task itself ends
up *failed*, listing every statement that raised (Settings › Manage
services › Scheduler). What a failing statement leaves behind is what it
always left behind: an assignment that raised leaves its name unset, so a
later line reading it fails too.

A statement whose service answers "not now" rather than "no" is a failed
statement like any other: it is listed in the task's `error` and in the
log, and nothing is re-run.

`name` can't shadow a reserved namespace or a core metric name (§2) —
rejected at build time. `name` is visible inside
a `task.defer(...)` lambda too, the same way `user`/`signal`/`env`
are — frozen at the moment `defer` runs, not re-evaluated later.

**`task.*`** is code-defined, not project-declared, and what it holds
depends on what this installation was built with: the three below are
always there, and each installed service adds its own — see the end of
this document. (`celebrate`/`notify`/`show`/`switch_to_human`/
`switch_to_ai` used to live here too; they moved to `chat.*`, reachable
only from `on-exit:` (§5.3bis), since only there does firing an action
have anything left to tunnel to the browser synchronously.)

- `task.defer(act, when)` — schedules another task call for
  later. `act` **must** be a zero-argument `lambda:` wrapping the real
  call; `when` **must** be `datetime.datetime(...)`/`.now(...)`,
  optionally ± one or more `datetime.timedelta(...)` — e.g.
  `task.defer(lambda: task.prompt('Reminder'), datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=env.reminder_days))`.
  A `timedelta`'s args may reference `env.*`/`signal.*`; `when` can never
  be a bare `env.<key>` or a string. Both rules, and the lambda's arity,
  are checked at build time. A deferred call is hibernated in the DB the
  moment `defer` runs (lambda source + a snapshot of `signal`/`env`/`user`
  at that moment), keyed to the user+project's published revision, rebuilt
  only when `when` arrives — restarts/deploys/republishes don't affect it;
  deleting the project or user removes it. Inside the lambda, `user`/
  `signal`/`env` read as snapshotted, while `task`/`metric`/`source`/
  `automaton` are live at run time (a deferred call may itself defer).
  `session.*`/`chat.*` are unavailable throughout `task`.
- `task.prompt(prompt)` — one extra synchronous model call, fully
  isolated from the conversation: no system prompt beyond `prompt`
  itself, no `general-prompt`/`contextual-prompt`, no attachments, no
  signal/env context, no chat history. `prompt` is the entire request.
  Returns its reply text for a later statement in the same script to
  use:

  ```yaml
  task: |
    greeting = task.prompt('Translate to Catalan: Nice to reach this state!')
  ```

  Nothing is persisted, and it never updates `env`/evaluates a signal/fires a
  transition — read-only, but its return value is real text. This is what
  replaced the old, removed `action-prompt` field, and what a project
  still carrying one is rewritten to call (§8).
- `task.websearch(query)` — searches the web for `query` and returns
  what the pages it finds say, as CSV. The search engine's own results
  are fetched and read, a model decides which columns describe them and
  fills them in, and the CSV text (header row first) comes back for a
  later statement in the same script to use:

  ```yaml
  task: |
    places = task.websearch('well-reviewed dentists in Barcelona')
    task.send_mail(user.email, places)
  ```

  This is the same search the Source card's own **AI Web Import** runs,
  minus its step-by-step progress: the CSV is the return value, and the
  project's own `sources:` files are untouched. The same CSV is also kept
  for the user now talking, under `cache/websearch/<project id>/<user
  id>`, which is what a source declared `url: websearch:user` (§5.2)
  reads — so a later turn, a trigger, or the model itself can query the
  table the script found, and a second search replaces the first.
  Read-only, like `prompt` — it always runs, actuators on or off.

**`drive.*`** is a file space of its own, one per (project, person) —
not `task.*`, but a bare statement of the same shape (`drive.write(...)`,
or the RHS of a `name = ...` local). Nothing about it is project
content: it starts empty, no `sources:`/`attachments:` entry ever names
it, it is left out of every export and every build, and it dies with
whichever of the project or the person goes first. Available in `task`
alone — a trigger and `on-exit` both run inside the turn, and this is
I/O that may one day be remote.

```yaml
task: |
  report = task.prompt('Summarize this conversation in ten lines.')
  drive.write('reports/last.md', report)
```

- `drive.read(path)` — exactly what was last written at `path` for the
  person now talking, text or bytes as it was written, or `""` if
  nothing has: a drive starts empty, and reading before writing is the
  normal case, not a failure.
- `drive.write(path, content)` — writes `content` at `path` verbatim,
  creating it or replacing whatever was there — `content` is text or
  bytes, stored and later read back exactly as given, never re-encoded,
  parsed, or reshaped into anything else. Anything other than text or
  bytes is refused. Returns the path written. The `/` inside `path` are
  part of the name, not folders to create; a leading `/` and repeated
  `/` are trimmed, so `'reports/last.md'` and `'/reports/last.md'` name
  the same file. `..` is rejected — there is nothing above the person's
  own space to reach.
- `drive.save_as_pdf(path, content)` — same as `drive.write`, but
  `content` is converted to a PDF first. What `content` already is —
  an image, a PDF (stored verbatim, no conversion), CSV, or text (markdown
  rendered, since that's the common case: a `task.prompt(...)` result) —
  is worked out from `content` itself, never from `path`'s extension,
  the same way `drive.write` never looks past what it's handed. Anything
  else is rejected.
- `drive.list(prefix)` — every path under `prefix`, in order; `""`
  means everything this person has.
- `drive.delete(path)` — removes one file, `True` if there was one.
- `drive.downloads(path)` — how many times this person has downloaded
  the file at `path` from the PDF preview dialog's Download button, `0`
  before the first one. The Save button in the same dialog adds a file
  to the drive without counting; Download does both.

A write also records, silently, which session (if any) fired the task
that produced it — `None` for a `task.defer`red call, the firing session
otherwise. No script ever reads this back; it exists only so a file
written from a *test* session is swept the moment that session is
deleted or superseded, the same way `cache/sessions/<id>/` already is —
a live session's own files are never touched by this and outlive it, by
design.

**No `task.*` call tunnels anything to the browser.** Every member
returns `None`, a plain value for an assignment, or a bool, never a
JsSnippet; only `on-exit`'s own `chat.*` calls tunnel JS now (§5.3bis).
A call's own return value is still available to an assignment.

**A real side effect never runs in a draft/test conversation** unless
actuators are explicitly enabled for it — while off (the default there)
it is suppressed and reported back as a `notify(...)` toast describing
what would have happened instead. `prompt` has no real-world side effect
to suppress, so it always runs.

## 6. Attachments

A filename under any `attachments:` (global, a signal's, or a state's —
actions have no `attachments:` of their own, §5) must be present
alongside `index.yml` — missing files fail validation by name.

| Extension | Sent as | Guaranteed to reach the model? |
| --- | --- | --- |
| `.yml`, `.yaml`, `.md`, `.txt`, `.csv` | Literal text, inlined into the prompt | **Yes** — every provider. |
| Anything else (e.g. `.pdf`, `.docx`) | Raw bytes, base64, `application/octet-stream` | **No** — provider-dependent (Gemini/OpenAI-driver providers drop it silently; Anthropic passes it through, not guaranteed useful). |

For anything that must reliably reach the model, use a text extension.
Every applicable attachment list is prepended as one synthetic (never
persisted) "user"/"assistant: Understood." exchange ahead of the real
conversation.

## 7. `init-action`

```yaml
init-action:
  target: lobby
  on-exit: chat.celebrate()
```

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `target` | **yes** | string | Starting state — must be a real key under `states:`. |
| `task` | no | string | Same mechanics as any action's (§5.4), scheduled as a task (delivered over the websocket) each time init-action fires. |
| `on-exit` | no | string | Same mechanics as any action's (§5.3bis), run each time init-action fires. When `target` is a `system` state (§4.1), its `chat.write(...)` is the opening message. |
| `env` | no | mapping key → expression | Same mechanics as any action's (§5.3), applied each time init-action fires — the place to reset a key a previous case left behind. It writes the key whether or not it already has a value. |

A mapping, not a list item — otherwise a regular action with no
`name`/`ui-label`/`trigger` (fixed internally).

**It is an action, executed as one.** The init-action goes through the
same transition path every other action does, in the same order: its
`env:` and `on-exit` are evaluated against one scope and written, its
`task` is scheduled, and a transition row from the implicit initial
state `""` to `target` is recorded with origin `init-action`. Before it
fires, every declared key that has no value yet is backfilled with its
own default (§5.3) — a separate, implicit action of its own, so the
init-action's `env:` may read the defaults and override them.

**When it fires.** At one moment only: the creation of a session, before
anything has looked at that session's state. A test or preview session
always restarts, so it always fires; a live session fires when
`project.new-session-strategy` is `restart` (§1.1), or — under `resume` —
when the automaton has never run for *this* user in a *live* session.
A live session under `resume` that finds such a state inherits it and
fires nothing.

That single event is the automaton starting, or restarting. What it is
not is "a session was created": a live session under `resume` is created
and starts nothing. And the request that created it does not matter
either — `session.create` makes one outright, `session.enter` makes one
whenever it finds no open conversation to enter (see docs/BUS.md), a
phone channel makes one to record a reply nobody asked for, and the
automaton restarts in all of them.

Re-entering an open conversation creates nothing, so it fires nothing:
there is no second introduction to suppress.

**What restarting clears.** The env persisted for that project and user,
and the model's own `global` memory, are wiped as the first step of the
same event — because the automaton is starting over, not because the
strategy is spelled `restart`. A `local`-scope memory needs no wiping: it
is per session, and a new session's is empty (§4).

## 8. Validation checklist

Every rule below must hold for a project to be valid — roughly in order
of how you're likely to hit them:

- `project.id` (§1.1) present, a valid Python identifier — no dots/hyphens/spaces.
- `project.revision` (§1.1), if given, a non-negative integer.
- `states:` present, a mapping, no key `""`.
- `init-action` present, a mapping, non-empty `target` naming a real state.
- Every state entry is itself a mapping (a common mistake: `actions:`
  indented as a sibling of the state key instead of nested under it —
  YAML happily parses that as its own separate, invalid state).
- Every state declares `input-processor`, `ai` or `system` (§4.1).
- Every `ai` state has a `contextual-prompt`.
- No script of a `system` state references `signal.*`.
- `chat.write(...)` / `chat.write_table(...)` appear only in the
  `on-exit` of an action whose target is a `system` state.
- No state declares `fixed-message` — refused naming its replacement.
- Every state's `transition-log-level`, if given, is a valid level.
- Every state's `signal-tracking-strategy`, if given, is `relevant` or `all`.
- Every state's `ai-memory-scope`, if given, is `none`, `local`, or `global`.
- Every action's `target` (incl. `init-action`'s) names a real state (or is a self-loop).
- Every action's `trigger`, if given: syntactically valid and every
  reference resolves (§5.2's rules per namespace).
- Every action's `trigger` compares a `signal.<name>` only against a
  number between 0 and 100 (§5.2) — the domain every signal value is
  coerced to (§3.1).
- Every action's `env`, if given: a mapping, each expression validated the same way as `trigger`.
- Every action's `task`, if given: one `task.<name>(...)` call (or
  `name = <expr>` assignment — §5.4) per non-blank line, validated the
  same way plus its own argument-count check; an assignment's `name` may
  not shadow a reserved namespace or core metric, and may only be
  referenced by a *later* line.
- Every action's `on-exit`, if given: one `env.<key> = expr` assignment,
  `name = expr` local or bare `chat.<method>(...)` call per non-blank
  line — §5.3bis — each env assignment's `key` already declared under
  top-level `env:` and its expression validated the same way as `env:`'s
  own (including its type-consistency check against that key's declared
  default), each local under task's own rules (no reserved name, read
  only by a later line), each `chat.*` call validated the same way plus
  its own argument-count check.
- Every `source.<name>.<method>(...)` call, wherever it appears: its
  arguments must bind to the driver method's own signature — a missing or
  unexpected argument names itself in the build error, alongside the
  expected signature — and two adjacent string literals in an argument
  (`'caso' '='`, a missing comma Python would silently join) are refused.
- No signal named after a reserved core metric (§2).
- Every `attachments:` entry (global/signal/state — actions have none) names a file actually present alongside `index.yml`.
- Every `sources:` entry's own `url`, if set, has a recognized driver scheme, and (for `avance:<path>`) its path names a file actually present alongside `index.yml`.
- Every name in an `ai` state's own `input`/`output` (§4.3) names a key actually declared in `env:`, that env key declares its own `ai-definition`, and it is not a `list` key (§5.3). A `system` state's `input`/`output` are ignored, not checked (§4.1).
- Every name in a state's own `ai-may-read-sources`/`ai-must-read-sources` (§4.2) names a source actually declared in `sources:`, that source declares its own `ai-definition`, its driver implements `select_rows_containing`, and no name appears in both fields for the same state. The old names `tools`, `ai-may-query-sources`, `ai-must-query-sources` are rejected with a message naming their replacement, and the removed `ai-may-write-sources` is rejected outright — the one place a build does say what a name used to be, because there is nothing that can settle it on the author's behalf (§8.2).

### 8.1 A field nobody reads

Every section takes the fields it reads and no others — `project`,
`signals`, `reactions`, `env`, `sources`, `states`, an action,
`init-action`, and the top level itself. A name outside its section's own
set is a build error: `chat: false` sat on 83 states of one real
installation, every one of them answering the user it was written to
silence, and nothing said a word.

A build knows what it reads and nothing else. It does not know that
`chat` used to be that field's name, or that `on-enter` was `task` — a
spelling the format has moved past is refused exactly like a typo,
because telling them apart is not a build's job. It is
`automaton/deprecations.py`'s, and the only thing that reads it is the
modernizer (§8.2), which settles what it can before a build ever sees the
file.

One pass reports all of them at once, each with its own line. A build
that stopped at the first would cost a whole pass per mistake, and five
bad fields would take five rounds of fixing and rebuilding; every problem
the pass can be carried out in spite of is collected and raised together
(`BuildCursor.reject`), so the design view can list them as one set of
places to go.

### 8.2 What is rewritten, and what is not

A stored revision that fails this checklist because the format moved on
underneath it is not left for a person when it does not have to be.
`automaton/deprecations.py` is the list of spellings that still have an
exact meaning today — `project.talk-enabled` is `services: {talk: …}`,
an action's `actuator`/`on-enter` is its `task`, its `action-prompt` is a
`task.prompt(...)` call in that `task`, a state's `chat` is
`chat-enabled`, its `on-enter` is the `task` of every action that reaches
it, its `ai-memory-strategy` is `ai-memory-scope` (`keep`/`clear` renamed
to `global`/`local`, §4), an env key's `ai-access`/`ui-label`/`value` are
gone with nothing in their place, its `ui-description` is its
`ai-definition` where that is still empty, its type `choice` is `list`
(§5.3), a state with no `input-processor` is an `ai` one (§4.1: before
the field existed, every state answered through the model — the one key
the modernizer adds, because its absence is a fact about the file's age,
not a guess about the author) —
and the modernizer rewrites them, in place, wherever an
`index.yml` enters or is built: on import, when a stored revision
fails to build, and once at boot for every project's head and published
revision (`project/archive/index_yml_migration.py`), so that what a
product serves recovers without anyone visiting. The design view has no
call of its own for it: opening a project loads it, and a load is where a
stale spelling is settled.

It runs in rounds until a round finds nothing, because one rewrite
uncovers the next: a script moved off a state onto the actions that reach
it arrives carrying calls in a namespace that has its own replacement.
Two properties hold throughout. It rewrites the field **where the field
is** — one written on an anchor that entries merge (`<<: *action`) is the
anchor's, and rewriting the entry that merges it would leave the original
to be merged in again. And what it writes is never something it still
recognizes, so opening the same project twice reports a fix exactly once.

What it will not do is choose. `actuator.notify(..., actuator.prompt(...))`
was one call reaching both the conversation and a service, and today's
format has no single line for that: the whole action is left as written,
including its field name, since renaming it alone would move a refusal
rather than remove one. The rest of the file is still repaired, and what
is left is refused with everything else — a person decides it.

Nor does it invent. An env key's `type` (§5.3) has no former spelling to
rewrite from, and guessing one from the key's name would be a choice: a stored
revision that declares a key without `type` is a broken project, refused
with the key and the four admitted values named, and handled the way any
other broken revision is — listed in the design view, fixed by a person.
A `fixed-message` state (§4.1) is the same: its text belongs in the
`on-exit` of the actions that reach it, and which of them, and in what
words, is the author's to say.

## 9. Worked examples

**Minimal** (the "Hello world" sample project):

```yaml
init-action:
  target: Hello

states:
  Hello:
    input-processor: ai
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
```

**A step-by-step questionnaire without the model** (the "Burn out"
sample's `step-n`): the state is `input-processor: system`, chat is off,
each press of a `choice.frequency` button runs the `on-exit` of `next`,
which scores the answer, advances `env.step` and says what comes next
with `chat.write('Step %d of %d' % (env.step + 1, len(env.questions)))`;
the last press fires `end` into an `ai` state that writes the report.

**Signals/metrics/triggers**: the "Metrics Playground" sample (self-looping)
and its "Metrics Playground (states)" sibling (each trigger lands on its
own final state) — one signal + one action per core metric, with an
extensive comment block on exercising each one.

**Richer real-world examples**: the "default" sample (multiple signals
with attachments, per-state `transition-log-level`); "Aprendr català"
(`history-cutoff`, `on-exit: chat.celebrate()`,
`on-exit: chat.notify(...)` surfacing grammar hints as toasts);
"Drogodependencia" (simpler, neither).
