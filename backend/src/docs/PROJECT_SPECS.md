# Project format specification (`index.yml`)

Authoritative reference for the `.yml`/`.zip` "project" format that drives
the Avance state engine. This document is exhaustive and self-contained:
following it precisely is enough to build a valid project with no other
context, whether by hand or programmatically (e.g. an LLM generating one).

The parser/validator is `backend/src/automaton/automaton_builder.py`
(`AutomatonBuilder.build`) — every rule below is enforced there, at
project upload time and at backend boot.

## 1. What a project is, on disk/in the API

A project is one YAML file, `index.yml`, plus zero or more attachment
files it references by name. It's uploaded as either:

- **A `.zip` archive**, `index.yml` at its root plus attachments **flat**
  (no subdirectories) alongside it. This is the only format that can
  carry attachments of its own.
- **A lone `.yml`/`.yaml` file** — becomes the project's `index.yml` with
  no attachments; if it references `attachments:` anywhere, those files
  must already exist for that project from a previous zip upload.

`PUT /api/projects/{project_name}` accepts either, told apart by the
request's `Content-Type` (or by sniffing the zip signature). The project
is fully validated before anything is committed — an invalid upload
changes nothing.

Two directories in this repo are relevant to projects, but neither is
read by the running backend:

- `backend/samples/` (this directory) — example projects for local
  development: `<name>/index.yml` (+ attachments) plus a matching
  `<name>.zip` built from it, kept in sync by hand. Upload one via the
  UI's Projects → Upload, or `PUT /api/projects/{name}` with the zip's
  bytes, to try it.
- `backend/tests/` — reuses the same zips as test fixtures.

The backend itself stores every project as a zip blob in its database
(there is no `projects/` folder on disk at runtime) — see the root
`README.md` for that part of the architecture; this document only covers
the file format.

## 2. Top-level fields

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `avance-version` | no | string | — | The Avance version this file was last saved with. Purely informational — the engine never reads or validates it. Should be the first line of the file by convention. |
| `init-action` | **yes** | mapping | — | Where the conversation starts. See §6. |
| `states` | **yes** | mapping (name → state) | — | Every state in the automaton. See §5. Must contain the state named by `init-action.target`. |
| `signals` | no | mapping (name → signal) | `{}` | Numeric values the model estimates from the conversation each turn. See §4. |
| `general-prompt` | no | string | `""` | Appended to every state's `contextual-prompt` when building the system prompt for a normal chat reply (never for a `fixed-message` state, and used *alone*, without the state's own prompt, when generating an `action-prompt` reply — see §6.3). |
| `attachments` | no | list of filenames | `[]` | Global attachments, sent with **every** model call that also sends `general-prompt` (i.e. every normal chat turn and every `action-prompt`). See §7. |
| `project` | no | mapping | — | Project identity/display metadata, and the auto-tracking mode toggle. See §2.1. |

Any other top-level key is ignored (not an error).

### 2.1 `project:`

```yaml
project:
  id: my_project
  ui-label: My Project
  ui-description: A friendly description.
  signal-tracking-on-ai-message: false
  talk-enabled: true
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | no | string (valid identifier) | — | What *other* projects reach this one as, through `automaton.<id>.*` (§6.2). Global uniqueness across projects is enforced separately, not here. |
| `ui-label` | no | string | — | Shown in the frontend (Projects list, session header). |
| `ui-description` | no | string | — | Shown in the frontend. |
| `signal-tracking-on-ai-message` | no | boolean | `false` | Selects one of two mutually exclusive auto-tracking modes: `false` (default) runs auto-tracking (signal computation + trigger evaluation) right after the user's message, before the model replies; `true` runs it instead after the model's reply (using signal values the model itself may have reported inline — see §4.3). |
| `talk-enabled` | no | boolean | `true` | Whether this project asks the model for `audio` metadata at all. `false` only narrows the server's own talk-service switch further — it can't turn audio on when the server has no talk service configured. |

## 3. Names, identifiers, and reserved words

- **State keys** (the keys under `states:`) are arbitrary non-empty
  strings, case-sensitive, matched literally by every `target:`. The key
  `""` (empty string) is reserved for the engine's own implicit
  bootstrap state and cannot be declared.
- **Action `name`** is required per action. Not required to be globally
  unique, but should be unique *within its own state* — `move()` resolves
  an action name against the current state's own action list and returns
  the first match.
- **Signal names** (the keys under `signals:`) **must be valid
  identifiers**: letters, digits, underscore, not starting with a digit
  (e.g. `mood`, `problem_recognition`). This isn't cosmetic: a trigger
  expression references a signal as `signal.<name>` (§6.2), parsed the
  same way a Python attribute access would be — `signal.my-signal >= 5`
  doesn't parse as one signal called `my-signal` at all. A hyphenated
  (or otherwise non-identifier) signal name will build without error but
  can never be referenced by any trigger.
- **Reserved names**: a signal cannot be named after a **core metric** —
  building rejects it outright. As of this writing the reserved set is
  exactly:

  ```text
  engagement
  retention
  activity_consistency
  state_stability
  signal_stability
  ```

  These are `metrics_framework`'s domain-agnostic metrics, computed from
  the stored conversation/session history (never from the model) — see
  `backend/src/metrics_framework/README.md`. Unlike a signal (`signal.
  <name>`), a metric is referenced as a **bare** name, with no namespace
  prefix — the two can appear interchangeably in the same expression
  (§6.2).

## 4. `signals:`

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

Each entry (keyed by the signal's name — see naming rules above):

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `definition` | **yes** | string | — | The instruction sent to the model when computing this signal — see §4.1. Free-form; the model isn't constrained to 0–100 by the engine, only by whatever `definition` itself asks for (the convention, followed by every example in this repo, is an integer 0–100 or a binary 0/100). |
| `ui-label` | no | string | the signal's name | Short label shown in the frontend (Inspector's Signals/Metrics-style tabs). |
| `ui-description` | no | string | `definition` | Short blurb shown in the frontend. Falls back to `definition` itself when absent, so it's always at least present. |
| `attachments` | no | list of filenames | `[]` | Sent **only** with the signal-computation call (see §4.1) — never with a normal chat turn, never with any other signal's computation. |

### 4.1 How signals are computed

Signals are **not** computed on every field access — only during
"auto-tracking" (gated by `project.signal-tracking-on-ai-message`, §2.1). When it runs, every declared
signal is evaluated **in one single model call**: the system prompt lists
every signal's `name` and `definition`, the user turn is a transcript of
the recent conversation (plus whatever attachments that turn's signals
declared), and the model is asked to reply with one JSON object mapping
every signal name to its value. A value that fails to parse (or a call
that fails outright) leaves that signal at `None` for this evaluation —
never a validation/build-time concern, a runtime one.

### 4.2 Reading a signal's current value

`GET /api/chat/signals` reports the latest computed value per signal (or
`None` before the first evaluation). This is what the frontend's Signals
tab, and `POST /api/triggers/preview`'s default signal values, are built
from — nothing in `index.yml` controls this directly.

### 4.3 Inline signal reporting

A model reply can also report signal values itself, inline, via a
reserved `<avance>...</avance>` JSON tag in its own output (parsed by
`chat/metadata_handler.py`) — this is a prompting convention, not an
`index.yml` field, mentioned here only so `signal-tracking-on-ai-message`
makes sense: when enabled, auto-tracking after the model's reply prefers
values reported this way over a fresh computation call.

## 5. `states:`

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
    actions: [ ... ]   # see §6
```

Each entry (keyed by its state key — see naming rules above):

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `contextual-prompt` | conditionally | string | — | System-prompt text for this state, combined with `general-prompt` for a normal chat reply (§2). **Required unless `fixed-message` is set** — see below, the two are mutually exclusive. |
| `fixed-message` | conditionally | string | — | A message the engine returns **verbatim in meaning**, translated into the user's language, instead of ever letting the model generate free-form content — see §5.1. Mutually exclusive with `contextual-prompt`; exactly one of the two must be present. |
| `ui-label` | no | string | the state's key | Shown in the frontend (graph, state bar, Inspector). |
| `ui-description` | no | string | `None` | Shown in the frontend; omitted entirely (not even an empty string) when absent. |
| `actions` | no | list of actions | `[]` | Outgoing actions — see §6. **A state with no actions is automatically `final`** (this isn't a separate field — `final` is derived, never declared). |
| `chat` | no | boolean | `true` | If `false`, a chat message sent while this is the current state is rejected outright (`409`) — the conversation can only proceed via one of this state's `actions`. Independent of `final`/`fixed-message`: neither implies this. |
| `history-cutoff` | no | boolean | `false` | If `true`, every message from *before* the most recent transition **into** this state is excluded — both from what the model sees on a normal chat reply, and from what auto-tracking evaluates signals against. Use it on a state that should reason only about what's been said since arriving there. Combined (not replaced) with the server-wide `chat-service.input-token-budget-per-turn` cutoff in `.config.yml` — see the root `README.md`'s "Configuring" section — which further trims backward from the latest message by cumulative token cost, regardless of state. |
| `transition-log-level` | no | one of `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `"WARNING"` | Log level used server-side whenever a transition **lands on** this state (i.e. it's a property of the destination, not the action). Purely operational (server logs), no effect on behavior. |
| `attachments` | no | list of filenames | `[]` | Sent with **every** model call this state is the "current" or "destination" state for — a normal chat reply (§2), and an `action-prompt` reply for an action landing here (§6.3). Not sent for a `fixed-message` state. |

### 5.1 `fixed-message` states

When the current state has `fixed-message` set, the model is **never**
asked to generate free-form content: every reply is instead a
translation of `fixed-message` into whichever language the user's last
message was written in, with an explicit instruction not to alter its
meaning, add anything, or react to what the user said. Typical use: a
safety/compliance message that must not be paraphrased by the model.
`contextual-prompt`, `general-prompt` and this state's `attachments` are
not used for that translation call.

## 6. `actions:` (nested under a state)

```yaml
actions:
  - name: advance
    ui-label: "User is ready to move on"
    ui-button: "Move on"
    target: next_state          # omit for a self-loop (stays on this state)
    trigger: "signal.mood >= 70 and engagement >= 20"
    action-prompt: |
      Briefly acknowledge the mood/engagement trigger, then continue.
    on-enter: actuator.celebrate()
    env:
      reset_counter: True
      number_of_steps: env.number_of_steps + 1
    attachments: []
```

Each entry:

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `name` | **yes** | string | — | Identifier for this action — what `POST /api/chat/sessions/{session_id}/action` (`action_name`) and `POST /api/triggers/preview`'s response reference. |
| `target` | no | string | this action's **own state's key** | The destination state. Omitting it (or setting it explicitly to the current state) is a **self-loop**: the conversation stays in the same state, only the action's own effects (transition logged, `action-prompt` reply if any) happen. Must name a real key under `states:` (or the current state itself). |
| `trigger` | no | string (expression) | `None` | A boolean expression over signal/metric names — see §6.2. Absent means **manual-only**: reachable only via `POST /api/chat/sessions/{session_id}/action`, never fired by auto-tracking. |
| `action-prompt` | no | string | `None` | An instruction sent to the model **as if it were the user's own message**, to produce an immediate reaction to this action firing — see §6.3. |
| `on-enter` | no | string | `None` | One or more `actuator.<name>(...)` calls, one per line — a side effect of the action firing, same timing as `env:` — see §6.5. A property of the action firing, not of its destination state: two different actions landing on the same state can each carry their own value (or none) — a state reached one way might celebrate, reached another way might not. |
| `env` | no | mapping of key -> expression | `None` | Updates the project's own environment memory when this action fires (manually or via a trigger) — see §6.4. |
| `ui-label` | no | string | `name` (also used if `ui-label` is present but empty) | Shown in the frontend. |
| `ui-button` | no | string | `ui-label`, and transitively `name`, if absent or empty | Button text in the frontend's manual-action bar. |
| `ui-description` | no | string | `None` | Shown in the frontend. |
| `attachments` | no | list of filenames | `[]` | Validated to exist (and shown in the Inspector UI) — **but never sent to the model**. An attachment that should reach the model when this action fires must be listed on the **destination state's own** `attachments:` (§5) or the top-level global `attachments:` (§2) instead. This is the one field on this list with no functional effect on model calls — don't rely on it for that. |

### 6.1 Manual vs. triggered firing

Any action — with or without a `trigger` — can be fired **manually** via
`POST /api/chat/sessions/{session_id}/action {action_name}`: this only checks that the
name exists among the current state's actions, the trigger expression
(if any) is never evaluated for a manual call.

Actions **with** a `trigger` are additionally evaluated automatically by
auto-tracking (§2, §4.1), after every signal computation, in **YAML
declaration order**: the first action in the current state whose
`trigger` evaluates to `true` fires — remaining ones are not evaluated
further for that turn, even if they'd also be `true` (FIFO priority).
`POST /api/triggers/preview` reports every triggerable action's own
result regardless, for inspection, without ever applying a transition.

### 6.2 Trigger expressions

A boolean-ish expression evaluated with
[`simpleeval`](https://pypi.org/project/simpleeval/) — comparisons,
boolean logic, and arithmetic, plus attribute access and calls **only**
on the six reserved namespaces below (never arbitrary Python — no
imports, no calling anything else):

```text
signal.mood >= 70
engagement >= 20 and retention >= 1
(signal.mood >= 40 and engagement >= 10) or signal_stability < 20
session.number_of_user_sessions() >= 3 and system.time() > "18:00:00"
user.role == "admin"
```

Six reserved namespaces, each resolving to a dict-or-proxy object:

| Namespace | Resolves to | Access |
| --- | --- | --- |
| `signal.<name>` | A signal declared in this project's own `signals:` | attribute (a value, or `None` before it's ever been computed) |
| `env.<name>` | A key set by **some action's own** `env:` field, anywhere in this project (§6.4) — never a model-reported free-form value | attribute |
| `system.<name>` | An engine fact independent of any user/session (`today`, `time`) | **call** — `system.today()`, not `system.today` |
| `session.<name>` | An engine fact about the current user+project's own session/transition history (`current_session_duration_in_minutes`, `last_user_session_datetime`, `number_of_user_sessions`, `state_duration_in_minutes`) | **call** — `session.number_of_user_sessions()`, not the bare attribute |
| `user.<name>` | A field of the current user's own account (`email`, `name`, `picture_url`, `provider`, `provider_user_id`, `created_at`, `last_login`, `active_project`, `role`) | attribute — same as `env.<name>`, never called |
| `source.<name>(...)` | A **data source** — see below | **call**, with its own arguments |

A **bare** (unnamespaced) name is only ever a core metric (§3) — nothing
else may appear unnamespaced anymore. `actuator.<name>(...)` is a
**seventh**, reserved but deliberately absent from both `trigger:` and
`env:` — it's only ever valid inside `on-enter:` (§6.5).

**Data sources** (`source.<name>(...)`) are code-defined "plugins" —
each one is its own Python module (`backend/src/tracking/sources/`),
not something a project declares in YAML. The only one that exists
today is `source.attachment(name)`: reads one of this project's own
uploaded files by name (exact path, or a unique basename), **directly
from storage** — not the `attachments:` mechanism (§5/§6) at all, and
not eagerly loaded the way an action's own declared attachments are.
This is deliberate: a project can bundle large reference files no
state/action/signal ever declares as an `attachments:` entry, without
paying to load and convert every one of them on every build — only the
one file actually named, only when a running trigger/env: expression
asks for it. Returns the file's **text** content; a binary file raises,
since there's no binary-to-text extraction. Reads at the exact revision
the current conversation's own automaton was loaded from — never
"whatever's published right now" — so mid-conversation this always sees
the same file content the rest of that conversation's automaton does,
even if the project gets edited/republished while it's still running.
Typically combined with `env:` (§6.4) to load a file's content into a
prompt-visible variable once, e.g. `policy: source.attachment('policy.md')`.
New data sources are a code change (a new module in that package plus
one dispatch method), not something a project author can add on their
own — this table just lists what's currently available.

Every reference is validated at build/upload time: `signal.<name>` must
name a declared signal, `env.<name>` must name a key **some** action's
own `env:` field sets somewhere in the project, `system.<name>`/
`session.<name>`/`user.<name>`/`source.<name>` must be
one of the fixed names above, and a bare name must be a recognized metric — anything
else fails with an "undefined name(s)" error, and a syntactically
invalid expression fails the same way with a parse error. At evaluation
time (not build time): if any referenced `signal.<name>` is currently
`None` (not yet computed —
e.g. before the first auto-tracking pass), the whole expression
short-circuits to `false` without attempting evaluation; any other
evaluation failure (an `env.<name>` never actually set yet, despite
being a recognized name — the project-wide declaration check only
proves the *name* is legitimate, not that a value has been set yet) is
logged and also treated as `false` — a trigger can never crash a turn.

### 6.3 `action-prompt`

When an action with `action-prompt` fires (manually or via a trigger),
the engine makes one extra model call **before** anything else that turn
(a normal reply, or the destination state's own opening message) would
have generated — exactly this, nothing more:

- **System prompt**: `general-prompt` alone (§2) — **not** combined with
  any state's `contextual-prompt`.
- **Attachments**: `attachments` (global, §2) + the **destination**
  state's own `attachments` (§5) — the action's own `attachments` field
  is never included (§6, last row).
- **User turn**: the literal text of `action-prompt` itself, appended
  after the real conversation history (subject to the destination
  state's `history-cutoff`, §5).

The reply becomes one more message in the conversation. If the action is
a self-loop, this is the *only* message generated for that turn — no
separate "opening message" is generated for re-entering the same state.
If it moves to a genuinely different state, the new state's own opening
message (if one would normally be generated there) follows afterward.

### 6.4 Action `env`

Every project also has a free-form **environment memory**: `key: value`
facts the model itself reports (always strings — see the
`[env]...[/env]` block every prompt embeds) persisted per user+project
and rendered back into every subsequent prompt. `system`/`session`
facts (§6.2) are never part of this — they're evaluation-scope-only,
never rendered into any prompt.

An action's own `env:` field updates a **separate**, deterministic part
of this memory — the `env.<name>` namespace §6.2's trigger expressions
themselves read from — as a **side effect of the action firing**
(manually or via its `trigger`). Each entry is `key: expression`,
evaluated with the exact same namespaced scope/mechanics as `trigger`
(§6.2: `signal.`/`env.`/`system.`/`session.`/`user.`/`source.`, plus any
bare metric name), just without the boolean cast — a result can be any simple value
(string, number, bool, `None`, ...), not only true/false:

```yaml
env:
  reset_counter: True
  number_of_steps: env.number_of_steps + 1
```

Self-referencing a key this same `env:` mapping also sets (as above) is
this field's own common case — and build-time validation allows it
because `env.<name>`'s own valid-name set is collected **project-wide**,
across every action's own declared `env:` keys, before any expression is
checked (§6.2) — so a key declared anywhere (including by the very
action referencing it) is always a legitimate `env.<name>` reference.
`env:` gets the **exact same** build-time validation `trigger` does —
both syntax *and* unknown-name checks.

At evaluation time, a failure (an `env.<name>` reference that's a
recognized name but has no value yet, or a runtime error like division
by zero) is never silent — it's logged as a warning, with the action
name, the key, and the underlying error, and that one key's previous
stored value, if any, is left untouched rather than being overwritten
with a spurious result. One bad key never blocks the others in the same
`env:` mapping. Updated values merge onto (rather than replace) the rest
of this action-set store, and the merge happens **before** anything else
that turn/transition generates a model reply for (`action-prompt`, the
destination state's own opening message, or a normal chat turn — §6.3,
§5) — so the very next prompt already reflects the new value.

Persisted separately from the model's own `[env]`-reported free-form
values, and it's this action-set store alone — never the free-form one —
that a trigger's own `env.<name>` reads from (§6.2): a free-form value
the model reports has no name known at build time, so it can never be
validated as a legitimate `env.<name>` reference the way an action's own
declared `env:` key can. The "Edit project" view's Inspector Env tab
shows the two in their own separate sections regardless — **ACTION** for
this action-set store, distinct from the model-reported **AI** one —
never editable/deletable through that UI, only ever a side effect of the
action that set them firing again.

### 6.5 Action `on-enter`

An action's `on-enter` field is one or more `actuator.<name>(...)`
calls, one per non-blank line, each evaluated with the exact same
namespaced scope/mechanics as `trigger`/`env` (§6.2, plus `actuator`
itself — see the reserved-namespaces note above) as a side effect of
the action firing (manually or via its `trigger`), same timing as
`env:` (§6.4):

```yaml
on-enter: |
  actuator.celebrate()
  actuator.notify('Nice!', 'You reached **state B**.')
```

**Actuators** (`actuator.<name>(...)`) are code-defined "plugins", same
shape as data sources (§6.2) — each one is its own Python method
(`backend/src/tracking/actuators/`), not something a project declares
in YAML. Three exist today:

- `actuator.celebrate()` and `actuator.notify(title, body_md)` (`body_md`
  is markdown) each compile straight to the frontend's own
  onEnterActions.js locals of the same name — a confetti animation and a
  toast, respectively. Nothing runs server-side beyond building that JS
  snippet: the "tunnel" is exact and unconditional, e.g.
  `actuator.notify('Nice!', 'Well done')` reaches the browser as the
  literal `notify("Nice!", "Well done")` and runs there exactly like an
  old-style bare on-enter script once did.
- `actuator.send_mail(to, body_md)` queues an email onto the system's
  own job queue — fire-and-forget, never awaited by the turn that
  triggered it — with no frontend-visible effect at all.

Every call's own return value (if any) becomes wire-ready JS the
frontend runs verbatim; a call with nothing to tunnel (`send_mail`)
simply contributes nothing. Multiple lines concatenate in order, so
`on-enter` can both celebrate and notify from the same action.

Actuators never run during a test replay/benchmark — that path never
even has a live actuator implementation wired in. A real side effect
(`send_mail`) also never runs during EditProjectView's embedded "Test"
chat unless that test session's own "Run actuators" toggle (Run panel)
is switched on; while off (the default), it's suppressed and reported
back as a `notify(...)` toast describing what would have happened,
instead of actually happening. `celebrate`/`notify` themselves carry no
real-world side effect to suppress, so they always tunnel through
regardless of that toggle.

## 7. Attachments

A filename listed under any `attachments:` (global, a signal's, or a
state's — **not** an action's, see §6) must refer to a file present
alongside `index.yml` in the same upload — missing files fail validation
by name (`"<field> attachment named '<file>' not found"`).

| Extension | Sent as | Guaranteed to reach the model? |
| --- | --- | --- |
| `.yml`, `.yaml`, `.md`, `.txt`, `.csv` | Literal text, inlined into the prompt | **Yes** — works with every configured AI provider. |
| Anything else (e.g. `.pdf`, `.docx`) | Raw bytes, base64-encoded, `media_type: application/octet-stream` | **No** — provider-dependent. Gemini and OpenAI-driver providers drop any non-text attachment silently (with a server-side warning log); the Anthropic provider passes it through as a generic binary document block, which is not guaranteed to render usefully for every file type. |

For anything that must reliably reach the model, **use a text
extension** (`.md`/`.txt` are the natural choices for prose). Delivery
mechanics, if useful background: every attachment list that applies to a
given model call is prepended as one synthetic (never persisted)
"user"/"assistant: Understood." exchange ahead of the real conversation
— see `chat/priming.py`.

## 8. `init-action`

```yaml
init-action:
  target: lobby
  action-prompt: |
    Greet the user briefly and explain what this conversation is about.
```

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `target` | **yes** | string | The state the automaton starts in — must name a real key under `states:`. |
| `action-prompt` | no | string | Same mechanics as any action's (§6.3) — fires once, the first time this project's conversation is opened. |
| `on-enter` | no | string | Same mechanics as any action's (§6) — sent alongside the very first state, the one time init-action itself fires. |

`init-action` is a mapping, not a list item, and is otherwise exactly
like a regular action (no `name`/`ui-label`/`trigger`/`attachments` of
its own to set — those are fixed internally).

## 9. Validation checklist

Everything below is enforced at upload/boot time — an invalid project is
rejected outright, nothing is left partially applied. In rough order of
how you're likely to hit them:

- `states:` is present, a mapping, and does not declare a key `""`.
- `init-action` is present, a mapping, with a non-empty `target` that
  names a real state.
- Every state entry is itself a mapping — a common mistake is an
  `actions:`/other field accidentally indented as a *sibling* of the
  state's key rather than nested under it, which YAML happily parses as
  its own separate (invalid) state.
- Every state has **exactly one** of `contextual-prompt` / `fixed-message`.
- Every state's `transition-log-level`, if given, is one of `DEBUG` /
  `INFO` / `WARNING` / `ERROR` / `CRITICAL`.
- Every action's `target` (including `init-action`'s) names a real state
  key (or is omitted/self-referential for a self-loop).
- Every action's `trigger`, if given, is syntactically valid and every
  reference resolves: `signal.<name>` to a declared signal, `env.<name>`
  to a key some action's own `env:` declares somewhere in the project,
  `system.<name>`/`session.<name>` to one of the fixed proxy methods,
  `user.<name>` to one of the fixed user fields, `source.<name>` to one
  of the fixed data sources (§6.2), and a bare name to a reserved metric name.
- Every action's `env`, if given, is a mapping, and each of its
  expressions gets the exact same validation `trigger` does — syntax and
  unknown-name checks alike (§6.4).
- Every action's `on-enter`, if given, is one `actuator.<name>(...)`
  call per non-blank line, each validated the same way (plus its own
  argument-count check against the real Python method) — see §6.5.
- No signal is named after a reserved core metric name (§3).
- Every `attachments:` entry (global, per-signal, per-state — not
  per-action) names a file actually present in the upload.
- A `.zip` upload: exactly one `index.yml` at the root, attachments flat
  (no subdirectories).

## 10. Worked examples

**Minimal** (no signals, no triggers — see `Hello world.zip`):

```yaml
init-action:
  target: Hello

states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
```

**Signals, metrics, and triggers** — see `Metrics Playground.zip` (a
self-looping variant, good for repeatedly exercising the same project
across many turns) and `Metrics Playground (states).zip` (a sibling where
each trigger lands on its own dedicated final state instead) — both
declare one signal and one action per core metric, including a trigger
that combines a signal and a metric in a single expression, with an
extensive comment block on how to exercise each one from a running
backend. Read either `index.yml` in full alongside this document as a
concrete, currently-valid reference.

For richer real-world examples: `default/index.yml` uses multiple signals
with attachments, a `fixed-message` state, and per-state
`transition-log-level`; `Aprendr català/index.yml` uses `history-cutoff`
alongside its own `fixed-message` state and several actions' own
`on-enter: actuator.celebrate()`.
`Drogodependencia/index.yml` is a further, simpler example with neither.
