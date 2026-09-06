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
alternative endpoint: a user message travels as a `turn` frame on the
single `/ws/notifications` connection a page holds, and that turn's own
`chunk`, `tool`, `done` and `error` frames come back on the same socket,
each carrying the `turn_id` the client minted — the only correlation
there is. Everything else (manual actions, session bootstrap, history,
project management) stays plain HTTP.

The order of a conversation is the order of the frames on that socket:
the server reads them one at a time and persists each user message right
there, in reading order, before any processing starts. Parallel HTTP
requests could never guarantee that, which is why the chat moved onto the
socket.

## 1. Top-level fields

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `avance-version` | no | string | — | Informational only, never read/validated. Conventionally the first line. |
| `init-action` | **yes** | mapping | — | Where the conversation starts. §5. |
| `states` | **yes** | mapping (name → state) | — | Every state. §4. Must include `init-action.target`. |
| `signals` | no | mapping (name → signal) | `{}` | Numeric values the model estimates each turn. §3. |
| `general-prompt` | no | string | `""` | Appended to a state's `contextual-prompt` for a normal reply. Never sent to an `actuator.prompt(...)` call (§5.4), which is fully isolated. |
| `attachments` | no | list of filenames | `[]` | Global attachments, sent with every call that also sends `general-prompt`. §6. |
| `env` | no | mapping (name → fields) | `{}` | Declares every `env.<name>` a trigger/env expression may reference, and — per key — what the model may do with it (`ai-access`). An action's `env:` (§5.3) can only update a key declared here. |
| `sources` | no | mapping (name → fields) | `{}` | Declares every `source.<name>` a trigger/env expression may reference, and the model may read/write as a tool. §5.2. |
| `project` | no | mapping | — | Identity/display metadata + auto-tracking mode. §1.1. |

Any other top-level key is ignored.

### 1.1 `project:`

```yaml
project:
  id: my_project
  family: com.example.suite
  revision: 3
  ui-label: My Project
  ui-description: A friendly description.
  signal-tracking-on-ai-message: false
  talk-enabled: true
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | **yes** | string, valid Python identifier | — | This project's own globally unique identity, and how *other* projects reach it via `automaton.<id>.*` (§5.2). Must satisfy `str.isidentifier()` — letters/digits/underscore, not starting with a digit; no dots/hyphens/spaces. |
| `family` | no | string, free-form | `None` | Visibility scope for `automaton.<id>.*` — never parsed/validated for format. Two projects can observe each other only if they declare the **exact same** `family` string. Unset means neither observes nor is observed by anything, including itself. |
| `revision` | no | non-negative integer | `0` | This project's own revision number, auto-stamped on every publish — don't hand-edit it going in. |
| `ui-label` | no | string | — | The only "name" ever shown to a user; `id` is never displayed. |
| `ui-description` | no | string | — | Shown in the frontend. |
| `signal-tracking-on-ai-message` | no | boolean | `false` | `false`: auto-tracking runs after the user's message, before the reply. `true`: runs after the reply instead (may reuse model-reported inline values, §3.2). |
| `talk-enabled` | no | boolean | `true` | Whether this project asks for `audio` metadata at all — can only narrow the server's own talk-service switch, never enable it. |

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
| `attachments` | no | list of filenames | `[]` | Sent only with this signal's own computation call. |

**3.1 Computation.** Only during auto-tracking (gated by
`project.signal-tracking-on-ai-message`, §1.1): every declared signal is
evaluated in **one model call** — system prompt lists every `name`+
`definition`, user turn is a recent-conversation transcript (+ each
signal's own attachments), model replies with one JSON object mapping
name → value. A value that fails to parse (or a failed call) leaves that
signal `None` for this pass — a runtime concern, never build-time.

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
    contextual-prompt: |
      Continue the conversation naturally.
    chat: true
    history-cutoff: false
    transition-log-level: WARNING
    attachments: []
    actions: [ ... ]   # see §5
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `contextual-prompt` | conditionally | string | — | System-prompt text, combined with `general-prompt`. **Required unless `fixed-message` is set** — exactly one of the two. |
| `fixed-message` | conditionally | string | — | Returned verbatim-in-meaning, translated to the user's language, instead of a free-form reply — §4.1. Mutually exclusive with `contextual-prompt`. |
| `ui-label` | no | string | the state's key | Shown in the frontend. |
| `ui-description` | no | string | `None` | Shown in the frontend; omitted entirely when absent. |
| `actions` | no | list of actions | `[]` | Outgoing actions — §5. **No actions ⇒ automatically `final`** (derived, never declared). |
| `chat` | no | boolean | `true` | `false`: a chat message here is rejected outright — only `actions` can proceed the conversation. Independent of `final`/`fixed-message`. |
| `history-cutoff` | no | boolean | `false` | `true`: excludes every message from before the most recent transition into this state, both from the model's view and from auto-tracking. Combines (doesn't replace) the server-wide token-budget cutoff in `.config.yml`. |
| `transition-log-level` | no | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `"WARNING"` | Log level when a transition **lands on** this state (property of the destination). Operational only. |
| `attachments` | no | list of filenames | `[]` | Sent with every normal reply this state is "current" for. Not sent for `fixed-message`, nor to an `actuator.prompt(...)` call (§5.4), which is fully isolated. |
| `ai-may-read-sources` | no | list of source names | `[]` | Sources whose `select_rows_*` reads the model may call, at its own discretion, while replying in this state — §4.2. |
| `ai-must-read-sources` | no | list of source names | `[]` | Same, but the read is forced once per entry into this state — §4.2. A source name can appear in at most one of the two read fields. |
| `ai-may-write-sources` | no | list of source names | `[]` | Sources whose `update` the model may call here — §4.2. Only a source whose driver implements `update` (today: `avance:env`, §5.2) may be listed; there is no `must-write`: a write is never forced. |

**4.1 `fixed-message` states.** The model is never asked for free-form
content: every reply is a translation of `fixed-message` into the user's
last-message language, instructed not to alter meaning/add/react.
Typical use: a safety/compliance message that must not be paraphrased.
`contextual-prompt`, `general-prompt`, and this state's `attachments` are
unused for that call.

**4.2 Native tool-calling.** `ai-may-read-sources`/`ai-must-read-sources`/
`ai-may-write-sources` each list names from this project's own top-level
`sources:` (§5.2) the model may use, mid-turn, as native tools while
replying in this state — one tool per (source, method): a source named
`flight_records` in a read field becomes one callable per read method its
driver supports (`source_flight_records_select_rows_containing`,
`source_flight_records_select_rows_where_column`,
`source_flight_records_select_rows_where_column_in_range`), one named
`env` in the write field becomes `source_env_update`. Reading and writing are independent grants; the two
read fields differ only in how much the model is trusted to decide for itself:

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
  the full catalog (every field's tools together). Whether this is "the
  first turn since entering the state" is decided by the backend, from
  the session's own transition/message history — **never left to the
  model to decide, and never re-askable by prompting alone.** Its purpose
  is to make the model *observe* the source's current values before it
  answers — for an `avance:env` source, to read the automaton's
  variables as this turn's own observation.
- **`ai-may-write-sources`** — the source's `update` (§5.2), at the
  model's discretion. Never forced: there is no `must-write` counterpart,
  and `update` is never in the forced set even when the same source is
  also in `ai-must-read-sources`.

A source named in any field must declare its own `ai-definition` (§5.2) —
a build error otherwise, the same requirement a signal's own `definition`
gets. The same source name can't appear in both read fields for one
state. A source in `ai-may-write-sources` whose driver has no `update`
fails the build with the same "undefined name(s): source.<name>.update"
message a script calling it would get. An `avance:env` source in
`ai-may-write-sources` but in neither read field of the same state builds
with a **warning** (the model would write variables it never sees), not
an error. An `avance:env` source in `ai-may-write-sources` with **no**
`ai-access: readwrite` key at all *is* a build error — the model's
`update` tool would have nothing it could ever write.

No field has a project-wide default, deliberately: a tool catalog costs
real tokens on **every** turn in that state, whether or not the model
ends up calling anything — each tool's own JSON schema plus the
provider's own function-calling overhead, on the order of ~1,000 tokens
per turn for three declared sources. Declaring the catalog per state
also documents *where* the model is allowed to look and write, not just
that it's allowed to — a state with none of the three fields sends the
exact same request a turn always did, before tool-calling existed at all.

Every tool takes the same arguments, whatever the driver:
`select_rows_containing` takes `values` (an array of strings — the row
filter, possibly empty); `select_rows_where_column` takes `column`,
`operator` (`=`, `!=`, `>`, `>=`, `<`, `<=`) and `value`;
`select_rows_where_column_in_range` takes `column`, `start` and `end`;
`update` takes `values` and `fields` (column → new value, at least one).
Every read returns whole rows — there is no column projection in the
model's own interface. A driver may *narrow* one of those schemas for the
model — `avance:env` restricts `fields` to the writable variables, each
described by its own `ai-definition` — but never changes their shape.

## 5. `actions:` (nested under a state)

```yaml
actions:
  - name: advance
    ui-label: "User is ready to move on"
    ui-button: "Move on"
    target: next_state          # omit for a self-loop (stays on this state)
    trigger: "signal.mood >= 70 and engagement >= 20"
    on-enter: |
      actuator.notify('Nice!', actuator.prompt('Write a short celebratory one-liner.'))
    env:
      reset_counter: True
      number_of_steps: env.number_of_steps + 1
    attachments: []
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `name` | **yes** | string | — | This action's own identifier — what a manual firing references. |
| `target` | no | string | this action's own state | Destination state; must be a real key (or the current state itself). Omitted/self-referential ⇒ self-loop (only the action's own effects happen). |
| `trigger` | no | string (expression) | `None` | Boolean expression over signal/metric names — §5.2. Absent ⇒ manual-only (never auto-fired). |
| `on-enter` | no | string | `None` | One or more `actuator.<name>(...)` calls, one per line — side effect of firing, same timing as `env:`. §5.4. Per-action, not per-destination-state: two actions landing on the same state can each carry a different (or no) value. |
| `env` | no | mapping key → expression | `None` | Updates the project's environment memory when this action fires. §5.3. |
| `ui-label` | no | string | `name` | Shown in the frontend. |
| `ui-button` | no | string | `ui-label`, then `name` | Manual-action button text. |
| `ui-description` | no | string | `None` | Shown in the frontend. |
| `attachments` | no | list of filenames | `[]` | Validated to exist — **never sent to the model**. To reach the model on firing, list it on the destination state's or the top-level `attachments:` instead. |

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
boolean logic, arithmetic, plus attribute access/calls **only** on the
namespaces below (never arbitrary Python — no imports, no other calls):

```text
signal.mood >= 70
engagement >= 20 and retention >= 1
(signal.mood >= 40 and engagement >= 10) or signal_stability < 20
session.number_of_user_sessions() >= 3 and session.state_duration_in_minutes() > 30
user.role == "admin"
```

| Namespace | Resolves to | Access |
| --- | --- | --- |
| `signal.<name>` | A declared signal | attribute (value, or `None` before first computation) |
| `env.<name>` | A key declared in top-level `env:` (§5.3) — never a model-reported free-form value | attribute |
| `session.<name>` | Engine fact about the current user+project session (`current_session_duration_in_minutes`, `last_user_session_datetime`, `number_of_user_sessions`, `state_duration_in_minutes`) | **call**, e.g. `session.number_of_user_sessions()` |
| `user.<name>` | Current user's account field (`email`, `name`, `picture_url`, `provider`, `provider_user_id`, `created_at`, `last_login`, `active_project`, `role`) | attribute |
| `source.<name>.<method>(...)` | A source declared in top-level `sources:` — below | method call, e.g. `.select_rows_containing(...)`/`.update(...)` |
| `automaton.<id>` | A different project's live state/env — below | `.state`, or `.env.<key>` |
| `datetime.<name>` | Python's `datetime`/`timedelta`/`timezone` only, mainly for `actuator.defer`'s `when` | call, e.g. `datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)` |

A **bare** name is only ever a core metric (§2) — nothing else may appear
unnamespaced. `actuator.<name>(...)` is reserved but only valid inside
`on-enter:` (§5.4), never in `trigger:`/`env:`.

**`automaton.<id>.*`** reads a different project's live state/env for the
same logged-in user: `automaton.<id>.state` (current state key, or `None`
with no session there) and `automaton.<id>.env.<key>` (that project's own
action-set env value — `<key>` must be declared in *its* `env:`). `<id>`
is a literal token, never an expression. Only reachable within the same
`family`: mismatched/missing family or unknown id all resolve to `None`
identically — indistinguishable by design. Enforced both at build time
(the reference must name a same-family id) and at runtime.

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
| `ai-definition` | conditionally | string | `None` | Written *for the model*: what this file contains and how to search it well. Becomes part of the tool's own description whenever this source is exposed as a native tool. **Required** (build error otherwise) for any source named in some state's own `ai-may-read-sources`/`ai-must-read-sources`/`ai-may-write-sources` (§4.2) — same requirement a signal's own `definition` gets; optional otherwise. |

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
undeclared source is. There is no hierarchy of source kinds: method
support is the whole compatibility story.

- `select_rows_containing(*values)` — grep-like lookup: the header row
  plus every **whole** row containing **every** given value
  (case-insensitive substring match, AND'd). No row at all satisfying the
  filter returns `""` — not even the header — so
  `source.<name>.select_rows_containing(...) != ''` is a real existence
  check ("the model proposes, the script verifies," below).
  `source.pino.select_rows_containing('VY3003', '2026-08-16')` finds the
  one row for that flight on that date.
- `select_rows_where_column(column, operator, value)` — the header row
  plus every whole row whose `column` satisfies the comparison.
  Operators: `=`, `!=`, `>`, `>=`, `<`, `<=`. Both sides are compared as
  numbers when both parse as numbers, as moments in time when both parse
  as ISO dates/datetimes (`YYYY-MM-DD`, `YYYY-MM-DD HH:MM`), and
  case-insensitively as text otherwise — so
  `source.pino.select_rows_where_column('data_partenza', '>=', '2026-08-16')`
  reads as a date comparison, not a string one.
- `select_rows_where_column_in_range(column, start, end)` — the same
  whole rows, for a `column` between `start` and `end`, **both included**
  (numbers and ISO dates alike):
  `source.pino.select_rows_where_column_in_range('data_partenza', '2026-08-01', '2026-08-31')`.
- `value(*values, key)` — the `key` cell of the *first* row satisfying
  the same filter as `select_rows_containing`, as a single scalar string;
  `""` if no row matches, an error *text* if `key` isn't a real column.
  Scripts and trigger/env: expressions only — never exposed to the model,
  which reads through the `select_rows_*` tools instead.
  `source.pino.value('VY3003', key='flight')` reads one field without
  parsing a table.
- `update(*values, fields={...})` — assigns `fields` (column → new value)
  to every row containing every value; returns how many rows it touched
  (`"1 row updated"`). Unsupported by a driver that can't write.

Every read returns whole rows: there is no column projection, and an
unknown column or operator comes back as an error *text*, never an
exception. Every driver implements `select_rows_containing`; the
column-filtered reads, `value` and `update` only where they make sense
for that driver (its own `SUPPORTED_METHODS`).

Two drivers exist today, both under the scheme `avance`:

**`avance:<path>` — an archive file.** Read-only access to one of this
project's own files, addressed by `url`'s own path (exact path or unique
basename under `behaviour/`, resolved directly from storage at the
conversation's own pinned automaton revision, never "whatever's published
now" — not the `attachments:` mechanism, nothing is eagerly loaded).
Assumes a normalized CSV (header + one row per record; the separator is
detected). Implements every `select_rows_*` read and `value` — never
`update`, read-only. A
whole-file read is `attachment.read(name)`'s job (on-enter only), not a
`source.*` capability.

**`avance:env` — the project's own env keys.** A one-row table whose
columns are the env keys with `ai-access` other than `none` (§5.3) — the
model's *only* channel for reading and, for a `readwrite` key, writing an
automaton variable. `url: avance:env`, no other parameter; `ui-label`/
`ai-definition` as for any source. `select_rows_containing()` returns the
header and the row (the column-filtered reads make no sense for a single
row and aren't implemented); `update(fields={...})`
writes the `readwrite` keys into the session's own env exactly as an
action's `env:` script would (persisted for a live session, ephemeral for
a test one), and refuses a `readonly` or unexported key as error text,
writing nothing. `value(key=...)` reads one exported key's value as a
scalar, script/trigger-only like every other source's `value`. Its
`update` schema is narrowed for the model: `fields` to the writable
variables, each described by its own `ai-definition`.
Declaring an `avance:env` source when no key has `ai-access` other than
`none` is a build error (an empty table); exported keys without an
`avance:env` source are fine (the key still serves scripts). A script may
call it too (`source.env.select_rows_containing()`, `source.env.value(key='pnr')`),
and is subject to the same `readwrite` check on `update`.

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

**5.3 Memory, env, and the model.** Two stores live per user+project,
with two different owners, and the names are load-bearing:

- **memory** — the model's own free-form notes (`key: value`, always
  strings), written only by the model through the `memory` field of its
  structured reply (a delta: only new/changed notes) and read only by the
  model, in the prompt's own "Current memory" block. No script or trigger
  ever sees it; the Inspector's Memory section shows and edits it.
- **env** — the automaton's declared variables: the project's top-level
  `env:` keys, deterministic, written by an action's own `env:` field
  (below) — or, for a `readwrite` key, by the model through an
  `avance:env` source's `update` (§5.2) — and read by triggers, scripts
  (`env.<name>`) and, where a state allows it, the model. `session` facts
  (§5.2) are never part of either.

```yaml
env:
  reset_counter:
    ui-description: "Whether the counter was just reset."
    value: "False"
  number_of_steps:
    value: "0"
  pnr:
    ui-description: "The booking's record locator."
    ai-access: readwrite
    ai-definition: The 6-character record locator the customer gives you; empty until they do.
    value: ""
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `value` | no | string (expression) | `""` | The default, applied once (top-to-bottom order) the first time a session opens — a later default may reference an earlier key. |
| `ui-description` | no | string | `None` | Shown in the frontend — never sent to the model. |
| `ai-access` | no | `none`/`readonly`/`readwrite` | `none` | What the *model* may do with this key, in absolute terms — through an `avance:env` source (§5.2) and only in a state that lists that source (§4.2). `none`: the model never sees it. `readonly`: it appears in the prompt's env block and in `select_rows_containing`. `readwrite`: it may also be written with `update`. Scripts are never subject to this: an action's `env:` writes any key. |
| `ai-definition` | conditionally | string | `None` | Written *for the model*: what this variable means. **Required** (build error) whenever `ai-access` isn't `none` — same requirement a source exposed to the model gets; optional otherwise. Becomes the field's own description in the `update` tool's schema. |

An action's `env:` can only update a key declared here, never invent one
(fails build validation otherwise). Declaring a key here doesn't by
itself update it on any turn. Existing projects need no change: every
key keeps `ai-access: none` until its author decides otherwise.

**The prompt's env block.** The model sees the automaton's env only in a
state whose `ai-may-read-sources`/`ai-must-read-sources` lists an
`avance:env` source; there, the system prompt ends with a "Current
environment" block — one `key: value` line per key with `ai-access` other
than `none`, every value cut to 200 characters with a
`[response too long — provide more specific filters via a select_rows_* read]` pointer,
placed last so its per-turn changes never invalidate the cacheable prefix. Anywhere else the
block does not exist, not even empty. The memory block is a separate
block with its own heading, and the model is told to change variables
only with `update`, never in the `memory` field — a *declared* key
named there is discarded outright (`Env.update`'s own `declared_keys`
filter), whether or not that key has been set yet: the model reporting
`pnr` as a memory note before ever calling `update` on it must not let
it leak into memory instead of being silently dropped. A project that
declares nothing (the Hello world sample) has no tools and no env
block: just the memory.

**The model proposes, the script verifies.** The intended way to use the
binding: give the model `readwrite` keys for the facts it has to collect
from the user (`pnr`, a corrected `flight`), let it `update` them, and
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
transition. An `update` the model made during the discarded reply is
already persisted when the regeneration starts, is **not** rolled back,
and the regeneration in the new state sees it as the current value. This
is deliberate: the write was the model's decision on the user's message,
not on its own discarded wording.

**Persistence and reset.** `readwrite` keys persist per (project, user)
like every other env key — across sessions. To start a case afresh, reset
them on the initial action (`init-action`'s own `env:`, §7) or on the
action that opens the case.

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
anything else that turn generates a reply (this action's own `on-enter`,
the destination state's opening message, or a normal chat turn) — the
very next prompt already reflects it.

Persisted separately from the model's own memory; only this action-set
store feeds a trigger's `env.<name>`. The action-set one is never
directly editable in the Inspector — only ever a side effect of its
action firing again, or of the model's own `update` on a `readwrite` key.

**5.4 Action `on-enter`.** One or more statements, one per non-blank
line, same namespaced scope as `trigger`/`env` (§5.2) as a firing side
effect, same timing as `env:` — except it additionally sees `actuator`
and does **not** see `session.*`/`session.metric.*` (a call may be
deferred past the firing session's own lifetime, so the whole scope is
built without a session rather than allowing it selectively). Each
statement is either an `actuator.<name>(...)` call, or a simple
`name = <expr>` local-variable assignment — the only other shape
allowed — making `name` usable, bare, by every *later* statement in this
same on-enter script (never an earlier one, never a different action's
own on-enter). This exists to let one `actuator.prompt(...)` call's
result reach more than one later call without re-running the model each
time:

```yaml
on-enter: |
  actuator.celebrate()
  actuator.notify('Nice!', 'You reached **state B**.')
  translated = actuator.prompt('Translate to Catalan: The party starts at 9pm.')
  actuator.notify('Recap', translated)
  actuator.send_mail(user.email, translated)
```

**Every on-enter script runs as a task, never inside the request that
fired it.** The transition and the action's `env:` writes are applied
synchronously (they feed the very next prompt); the script itself is
hibernated in the database as a task due immediately and executed by a
background worker — `actuator.prompt` is a model call and
`actuator.send_mail` a network call, and neither belongs in a chat
turn's own response time. Consequently whatever the script tunnels
(`celebrate()`, `notify(...)`, `show(...)`) reaches the browser over the
websocket as a `notification` frame, a moment after the turn's own
response, never inside it; a script that fails to evaluate is logged
and its task settles with nothing to push, exactly as the in-turn
evaluation used to skip a failing line. `actuator.defer` (below) is the
same task with a later due time.

`name` can't shadow a reserved namespace or a core metric name (§2) —
rejected at build time. Assigning is itself never tunneled to the
frontend, even when `<expr>` alone would have been (e.g.
`x = actuator.celebrate()`); referencing `name` bare on a later line is
what tunnels it, if it's still JS at that point. `name` is visible inside
an `actuator.defer(...)` lambda too, the same way `user`/`signal`/`env`
are — frozen at the moment `defer` runs, not re-evaluated later.

**Actuators** are code-defined plugins, not project-declared. Seven exist:

- `actuator.celebrate()` / `actuator.notify(title, body_md)` / `actuator.show(body_md)` —
  compile straight to `onEnterActions.js` locals of the same name
  (confetti / toast / dialog). Nothing runs server-side beyond building
  that JS snippet — the tunnel is exact, e.g. `actuator.notify('Nice!', 'Well done')`
  reaches the browser as literal `notify("Nice!", "Well done")`. `show`
  renders `body_md` (markdown) in the app's existing generic dialog
  (DialogHost.vue) rather than a toast — no title, closed via its × button.
- `actuator.send_mail(to, body_md)` — queues an email on the job queue,
  fire-and-forget, no frontend-visible effect.
- `actuator.whatsapp(phone_number, message_md)` — sends a WhatsApp
  message to `phone_number` (E.164 digits, `+` optional) through the same
  Cloud API the WhatsApp channel itself sends replies with, markdown
  converted the same way. Unlike `send_mail` it isn't fire-and-forget:
  the on-enter task blocks on the API call and the statement's own value
  is `True` once it's accepted, `False` — nothing sent — for a
  `phone_number` with no linked user account or a failed API call, so a
  script can react to it, e.g. `sent = actuator.whatsapp(to, body)`. Once
  sent, `message_md` is also appended as an `assistant` message to the
  recipient's own live session on *this* action's project (the one
  bound to the actuator set the on-enter script is running under, not
  necessarily the recipient's own active project) — the recipient's
  currently open one if there is any (any channel), or a freshly opened
  `whatsapp-chat` one otherwise. Best-effort: a failure recording it
  never turns a successful send back into `False`.
- `actuator.defer(act, when)` — schedules another actuator call for
  later. `act` **must** be a zero-argument `lambda:` wrapping the real
  call; `when` **must** be `datetime.datetime(...)`/`.now(...)`,
  optionally ± one or more `datetime.timedelta(...)` — e.g.
  `actuator.defer(lambda: actuator.send_mail(user.email, 'Reminder'), datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=env.reminder_days))`.
  A `timedelta`'s args may reference `env.*`/`signal.*`; `when` can never
  be a bare `env.<key>` or a string. Both rules, and the lambda's arity,
  are checked at build time. A deferred call is hibernated in the DB the
  moment `defer` runs (lambda source + a snapshot of `signal`/`env`/`user`
  at that moment), keyed to the user+project's published revision, rebuilt
  only when `when` arrives — restarts/deploys/republishes don't affect it;
  deleting the project or user removes it. Inside the lambda, `user`/
  `signal`/`env` read as snapshotted, while `actuator`/`metric`/`source`/
  `automaton` are live at run time (a deferred call may itself defer).
  `session.*` is unavailable throughout `on-enter`.
- `actuator.prompt(prompt)` — one extra synchronous model call, fully
  isolated from the conversation: no system prompt beyond `prompt`
  itself, no `general-prompt`/`contextual-prompt`, no attachments, no
  signal/env context, no chat history. `prompt` is the entire request.
  Returns its reply text for another actuator call to use (usually
  `actuator.notify`'s `body_md`):

  ```yaml
  on-enter: |
    actuator.notify('Nice!', actuator.prompt('Translate to Catalan: Nice to reach this state!'))
  ```

  Nothing is persisted, and it never updates `env`/evaluates a signal/fires a
  transition — read-only, like `send_mail`, but its return value is real
  text. This is what replaced the old, removed `action-prompt` field.

Every call's return value (if any) becomes wire-ready JS the frontend
runs verbatim, **except** `actuator.prompt(...)`'s — meant only for
another actuator call in the same line to consume (always wrap it, e.g.
in `actuator.notify(...)`; a bare `actuator.prompt(...)` line contributes
nothing). A call with nothing to tunnel (`send_mail`, `whatsapp`, `defer`)
contributes nothing either, even though `whatsapp`'s own `True`/`False`
is available to an assignment the same way `prompt`'s text is. Multiple
lines concatenate in order.

`send_mail`/`whatsapp`/`defer`/`actuator.prompt(...)` never run during a
test replay/benchmark. A real side effect (`send_mail`, `whatsapp`,
`defer`) also never runs in a draft/test conversation unless actuators
are explicitly enabled for it — while off (the default there) it's
suppressed and reported back as a `notify(...)` toast describing what
would have happened instead. `celebrate`/`notify`/`show`/`prompt` have
no real-world side effect to suppress, so they always run.

## 6. Attachments

A filename under any `attachments:` (global, a signal's, or a state's —
**not** an action's, §5) must be present alongside `index.yml` — missing
files fail validation by name.

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
  on-enter: actuator.celebrate()
```

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `target` | **yes** | string | Starting state — must be a real key under `states:`. |
| `on-enter` | no | string | Same mechanics as any action's (§5.4), fired (as a task, delivered over the websocket) the one time init-action fires. |
| `env` | no | mapping key → expression | Same mechanics as any action's (§5.3), applied on top of every declared key's own default the one time init-action fires — the place to reset a `readwrite` key a previous case left behind. |

A mapping, not a list item — otherwise a regular action with no
`name`/`ui-label`/`trigger`/`attachments` (fixed internally).

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
- Every state has **exactly one** of `contextual-prompt` / `fixed-message`.
- Every state's `transition-log-level`, if given, is a valid level.
- Every action's `target` (incl. `init-action`'s) names a real state (or is a self-loop).
- Every action's `trigger`, if given: syntactically valid and every
  reference resolves (§5.2's rules per namespace).
- Every action's `env`, if given: a mapping, each expression validated the same way as `trigger`.
- Every action's `on-enter`, if given: one `actuator.<name>(...)` call (or
  `name = <expr>` assignment — §5.4) per non-blank line, validated the
  same way plus its own argument-count check; an assignment's `name` may
  not shadow a reserved namespace or core metric, and may only be
  referenced by a *later* line.
- No signal named after a reserved core metric (§2).
- Every `attachments:` entry (global/signal/state, not action) names a file actually present alongside `index.yml`.
- Every `sources:` entry's own `url`, if set, has a recognized driver scheme, and (for `avance:<path>`) its path names a file actually present alongside `index.yml`.
- Every `env:` key's `ai-access`, if given, is `none`/`readonly`/`readwrite`, and one that isn't `none` declares its own `ai-definition`.
- An `avance:env` source (§5.2) is only declared when at least one env key has `ai-access` other than `none`.
- Every name in a state's own `ai-may-read-sources`/`ai-must-read-sources`/`ai-may-write-sources` (§4.2) names a source actually declared in `sources:`, that source declares its own `ai-definition`, its driver implements the method the field exposes (`select_rows_containing`/`update`), and no name appears in both read fields for the same state. The old names `tools`, `ai-may-query-sources`, `ai-must-query-sources` are rejected with a message naming their replacement.

A stored revision that fails this checklist because the format moved on
underneath it (a removed field, a removed method) is never rewritten
automatically — there is no migration for a breaking format change. The
project is paused, flagged broken, and stays that way until a human
corrects the `index.yml` by hand in the design view, whose banner shows
the builder's own rejection message.

## 9. Worked examples

**Minimal** (the "Hello world" sample project):

```yaml
init-action:
  target: Hello

states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
```

**Signals/metrics/triggers**: the "Metrics Playground" sample (self-looping)
and its "Metrics Playground (states)" sibling (each trigger lands on its
own final state) — one signal + one action per core metric, with an
extensive comment block on exercising each one.

**Richer real-world examples**: the "default" sample (multiple signals
with attachments, a `fixed-message` state, per-state
`transition-log-level`); "Aprendr català" (`history-cutoff`, a
`fixed-message` state, `on-enter: actuator.celebrate()`,
`actuator.notify(...)`/`actuator.prompt(...)` combos surfacing generated
hints as toasts); "Drogodependencia" (simpler, neither).
