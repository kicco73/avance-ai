# Project format specification (`index.yml`)

Authoritative, exhaustive, self-contained reference for the `.yml`/`.zip`
"project" format that drives the Avance state engine — enough to build a
valid project with no other context, by hand or programmatically (e.g. an
LLM generating one). Every rule below is enforced at upload time and at
backend boot.

## 1. What a project is, on disk/in the API

A project is one YAML file, `index.yml`, plus zero or more attachment
files it references by name. Uploaded as either:

- **A `.zip` archive** — `index.yml` at its root plus attachments flat
  (no subdirectories) alongside it. Only format that can carry attachments.
- **A lone `.yml`/`.yaml` file** — becomes `index.yml` with no
  attachments; any `attachments:` it references must already exist for
  that project from a previous zip upload.

`POST /api/projects/upload` accepts either (told apart by `Content-Type`
or by sniffing the zip signature) — no project id in the URL; the id
comes from the upload's own `project.id` (§2.1). Fully validated before
anything is committed — an invalid upload changes nothing. See §2.2 for
uploading over an id that already exists.

`backend/samples/` holds example projects for local dev (`<name>/index.yml`
plus attachments, plus a matching `<name>.zip`); `backend/tests/` reuses
the same zips as fixtures. Neither is read by the running backend — it
stores every project as a zip blob in its own database.

## 2. Top-level fields

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `avance-version` | no | string | — | Informational only, never read/validated. Conventionally the first line. |
| `init-action` | **yes** | mapping | — | Where the conversation starts. §6. |
| `states` | **yes** | mapping (name → state) | — | Every state. §5. Must include `init-action.target`. |
| `signals` | no | mapping (name → signal) | `{}` | Numeric values the model estimates each turn. §4. |
| `general-prompt` | no | string | `""` | Appended to a state's `contextual-prompt` for a normal reply; also sent alone ahead of an `actuator.prompt(...)` call (§6.4) — never combined with the state's own prompt in that case. |
| `attachments` | no | list of filenames | `[]` | Global attachments, sent with every call that also sends `general-prompt`. §7. |
| `env` | no | mapping (name → fields) | `{}` | Declares every `env.<name>` a trigger/env expression may reference. An action's `env:` (§6.3) can only update a key declared here. |
| `project` | no | mapping | — | Identity/display metadata + auto-tracking mode. §2.1. |

Any other top-level key is ignored.

### 2.1 `project:`

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
| `id` | **yes** | string, valid Python identifier | — | This project's DB primary key and how *other* projects reach it via `automaton.<id>.*` (§6.2). Must satisfy `str.isidentifier()` — letters/digits/underscore, not starting with a digit; no dots/hyphens/spaces. Globally unique — re-uploading an existing id adds a revision, never a second project (§2.2). |
| `family` | no | string, free-form | `None` | Visibility scope for `automaton.<id>.*` — never parsed/validated for format. Two projects can observe each other only if they declare the **exact same** `family` string. Unset means neither observes nor is observed by anything, including itself. |
| `revision` | no | non-negative integer | `0` | Auto-stamped on every publish; don't hand-edit — on upload it instead drives §2.2's logic. |
| `ui-label` | no | string | — | The only "name" ever shown to a user; `id` is never displayed. |
| `ui-description` | no | string | — | Shown in the frontend. |
| `signal-tracking-on-ai-message` | no | boolean | `false` | `false`: auto-tracking runs after the user's message, before the reply. `true`: runs after the reply instead (may reuse model-reported inline values, §4.3). |
| `talk-enabled` | no | boolean | `true` | Whether this project asks for `audio` metadata at all — can only narrow the server's own talk-service switch, never enable it. |

### 2.2 Uploading over an existing `id`

Adds a revision on the existing project, never a second one. Outcome
depends on the uploaded `project.revision` vs. the id's currently
published revision:

- **No `revision` declared** — accepted at `published + 1`.
- **`revision` greater than published** — accepted at exactly that number.
- **`revision` ≤ published** — **rejected outright**, nothing persisted (frontend shows a dialog, not the usual auto-dismissing banner).

Either accepted case publishes immediately — no separate draft step for
this endpoint. A brand-new id is created and published at its declared
revision (default `0`).

## 3. Names, identifiers, and reserved words

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
  namespaced values in the same expression (§6.2).

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

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `definition` | **yes** | string | — | Instruction sent to the model when computing this signal (§4.1). Free-form; convention is integer 0–100 or binary 0/100. |
| `ui-label` | no | string | the signal's name | Shown in the frontend. |
| `ui-description` | no | string | `definition` | Shown in the frontend. |
| `attachments` | no | list of filenames | `[]` | Sent only with this signal's own computation call. |

**4.1 Computation.** Only during auto-tracking (gated by
`project.signal-tracking-on-ai-message`, §2.1): every declared signal is
evaluated in **one model call** — system prompt lists every `name`+
`definition`, user turn is a recent-conversation transcript (+ each
signal's own attachments), model replies with one JSON object mapping
name → value. A value that fails to parse (or a failed call) leaves that
signal `None` for this pass — a runtime concern, never build-time.

**4.2 Reading.** `GET /api/chat/signals` reports the latest computed value
per signal (`None` before first evaluation) — what the frontend's Signals
tab and `POST /api/triggers/preview`'s default values come from.

**4.3 Inline reporting.** A model reply can self-report signal values via a
reserved `<avance>...</avance>` JSON tag — a prompting convention, not an
`index.yml` field. When `signal-tracking-on-ai-message` is on,
auto-tracking prefers these values over a fresh computation call.

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

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `contextual-prompt` | conditionally | string | — | System-prompt text, combined with `general-prompt`. **Required unless `fixed-message` is set** — exactly one of the two. |
| `fixed-message` | conditionally | string | — | Returned verbatim-in-meaning, translated to the user's language, instead of a free-form reply — §5.1. Mutually exclusive with `contextual-prompt`. |
| `ui-label` | no | string | the state's key | Shown in the frontend. |
| `ui-description` | no | string | `None` | Shown in the frontend; omitted entirely when absent. |
| `actions` | no | list of actions | `[]` | Outgoing actions — §6. **No actions ⇒ automatically `final`** (derived, never declared). |
| `chat` | no | boolean | `true` | `false`: a chat message here is rejected (`409`) — only `actions` can proceed the conversation. Independent of `final`/`fixed-message`. |
| `history-cutoff` | no | boolean | `false` | `true`: excludes every message from before the most recent transition into this state, both from the model's view and from auto-tracking. Combines (doesn't replace) the server-wide token-budget cutoff in `.config.yml`. |
| `transition-log-level` | no | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `"WARNING"` | Log level when a transition **lands on** this state (property of the destination). Operational only. |
| `attachments` | no | list of filenames | `[]` | Sent with every call this state is "current" for (normal reply, or `actuator.prompt(...)` — §6.4). Not sent for `fixed-message`. |

**5.1 `fixed-message` states.** The model is never asked for free-form
content: every reply is a translation of `fixed-message` into the user's
last-message language, instructed not to alter meaning/add/react.
Typical use: a safety/compliance message that must not be paraphrased.
`contextual-prompt`, `general-prompt`, and this state's `attachments` are
unused for that call.

## 6. `actions:` (nested under a state)

```yaml
actions:
  - name: advance
    ui-label: "User is ready to move on"
    ui-button: "Move on"
    target: next_state          # omit for a self-loop (stays on this state)
    trigger: "signal.mood >= 70 and engagement >= 20"
    on-enter: |
      actuator.notify('Nice!', actuator.prompt('Briefly acknowledge the mood/engagement trigger.'))
    env:
      reset_counter: True
      number_of_steps: env.number_of_steps + 1
    attachments: []
```

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `name` | **yes** | string | — | What `POST /api/chat/sessions/{session_id}/action` and `POST /api/triggers/preview` reference. |
| `target` | no | string | this action's own state | Destination state; must be a real key (or the current state itself). Omitted/self-referential ⇒ self-loop (only the action's own effects happen). |
| `trigger` | no | string (expression) | `None` | Boolean expression over signal/metric names — §6.2. Absent ⇒ manual-only (never auto-fired). |
| `on-enter` | no | string | `None` | One or more `actuator.<name>(...)` calls, one per line — side effect of firing, same timing as `env:`. §6.4. Per-action, not per-destination-state: two actions landing on the same state can each carry a different (or no) value. |
| `env` | no | mapping key → expression | `None` | Updates the project's environment memory when this action fires. §6.3. |
| `ui-label` | no | string | `name` | Shown in the frontend. |
| `ui-button` | no | string | `ui-label`, then `name` | Manual-action button text. |
| `ui-description` | no | string | `None` | Shown in the frontend. |
| `attachments` | no | list of filenames | `[]` | Validated to exist and shown in the Inspector — **never sent to the model**. To reach the model on firing, list it on the destination state's or the top-level `attachments:` instead. |

**6.1 Manual vs. triggered.** Any action fires manually via
`POST /api/chat/sessions/{session_id}/action {action_name}` — the trigger
(if any) is never evaluated for a manual call. Actions **with** a
`trigger` are also evaluated by auto-tracking after every signal
computation, in **YAML declaration order** — first `true` wins, FIFO,
remaining ones skipped that turn. `POST /api/triggers/preview` reports
every triggerable action's result regardless, without applying anything.

Leave an action manual-only (no `trigger`, no signal needed) when it's a
**deterministic** user choice rather than something inferred from what
they said — simpler on every axis (fewer signals to estimate, fewer
triggers to evaluate, and a UI button instead of something the system
might read wrong). Reserve `trigger` for transitions that genuinely
depend on interpreting the conversation.

**6.2 Trigger expressions.** A boolean-ish expression evaluated with
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
| `env.<name>` | A key declared in top-level `env:` (§6.3) — never a model-reported free-form value | attribute |
| `session.<name>` | Engine fact about the current user+project session (`current_session_duration_in_minutes`, `last_user_session_datetime`, `number_of_user_sessions`, `state_duration_in_minutes`) | **call**, e.g. `session.number_of_user_sessions()` |
| `user.<name>` | Current user's account field (`email`, `name`, `picture_url`, `provider`, `provider_user_id`, `created_at`, `last_login`, `active_project`, `role`) | attribute |
| `source.<name>(...)` | A data source — below | call with its own args |
| `automaton.<id>` | A different project's live state/env — below | `.state`, or `.env.<key>` |
| `datetime.<name>` | Python's `datetime`/`timedelta`/`timezone` only, mainly for `actuator.defer`'s `when` | call, e.g. `datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)` |

A **bare** name is only ever a core metric (§3) — nothing else may appear
unnamespaced. `actuator.<name>(...)` is reserved but only valid inside
`on-enter:` (§6.4), never in `trigger:`/`env:`.

**`automaton.<id>.*`** reads a different project's live state/env for the
same logged-in user: `automaton.<id>.state` (current state key, or `None`
with no session there) and `automaton.<id>.env.<key>` (that project's own
action-set env value — `<key>` must be declared in *its* `env:`). `<id>`
is a literal token, never an expression. Only reachable within the same
`family`: mismatched/missing family or unknown id all resolve to `None`
identically — indistinguishable by design. Enforced both at build time
(the reference must name a same-family id) and at runtime.

**Data sources** (`source.<name>(...)`) are code-defined plugins, not
project-declared. Two exist:

- `source.attachment(name)` — reads one of the project's own uploaded
  files by exact path or unique basename, directly from storage (not the
  `attachments:` mechanism — nothing is eagerly loaded). Returns text
  content; a binary file raises. Reads at the conversation's own pinned
  automaton revision, never "whatever's published now". Often combined
  with `env:` to load a file once, e.g. `policy: source.attachment('policy.md')`.
- `source.search(what, where)` — grep-like lookup over one of the
  project's attachments (same resolution/pinning rules as `source.attachment`).
  Assumes a normalized CSV (header + one row per record); returns the
  header plus every row containing `what` as a case-insensitive substring
  anywhere in the line. E.g. `source.search('Paris', 'geo/cities.csv')`.

New data sources are a code change, not something a project author adds.

Every reference is validated at build/upload time (`signal.<name>` must
be declared, `env.<name>` must be set by some action somewhere,
`session`/`user`/`source` names must be from the fixed lists, bare names
must be a recognized metric) — anything else fails with an "undefined
name(s)" or parse error. At evaluation time: a referenced `signal.<name>`
still `None` short-circuits the whole expression to `false`; any other
failure (e.g. an `env.<name>` never actually set) is logged and also
treated as `false` — a trigger can never crash a turn.

**6.3 Action `env`.** Every project has a free-form environment memory:
`key: value` facts the model reports (always strings, via the
`[env]...[/env]` prompt block), persisted per user+project. `session`
facts (§6.2) are never part of this. An action's own `env:` updates a
**separate**, deterministic part of it — the same `env.<name>` namespace
triggers read — whose valid keys are fixed by the project's top-level
`env:` (§2):

```yaml
env:
  reset_counter:
    ui-description: "Whether the counter was just reset."
    value: "False"
  number_of_steps:
    value: "0"
```

`value:` is the default, applied once (top-to-bottom order) the first
time a session opens — a later default may reference an earlier key.
An action's `env:` can only update a key declared here, never invent one
(fails build validation otherwise). Declaring a key here doesn't by
itself update it on any turn.

Each `env:` entry is `key: expression`, same namespaced scope/mechanics
as `trigger` (§6.2) minus the boolean cast — any simple value (string,
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

Persisted separately from the model's own free-form `[env]` values; only
this action-set store feeds a trigger's `env.<name>`. The Inspector Env
tab shows the two separately (**ACTION** vs. **AI**) — the action-set one
is never directly editable, only a side effect of its action firing again.

**6.4 Action `on-enter`.** One or more `actuator.<name>(...)` calls, one
per non-blank line, same namespaced scope as `trigger`/`env` (§6.2) as a
firing side effect, same timing as `env:` — except it additionally sees
`actuator` and does **not** see `session.*`/`session.metric.*` (a call may
be deferred past the firing session's own lifetime, so the whole scope is
built without a session rather than allowing it selectively).

```yaml
on-enter: |
  actuator.celebrate()
  actuator.notify('Nice!', 'You reached **state B**.')
```

**Actuators** are code-defined plugins, not project-declared. Five exist:

- `actuator.celebrate()` / `actuator.notify(title, body_md)` — compile
  straight to `onEnterActions.js` locals of the same name (confetti /
  toast). Nothing runs server-side beyond building that JS snippet — the
  tunnel is exact, e.g. `actuator.notify('Nice!', 'Well done')` reaches
  the browser as literal `notify("Nice!", "Well done")`.
- `actuator.send_mail(to, body_md)` — queues an email on the job queue,
  fire-and-forget, no frontend-visible effect.
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
- `actuator.prompt(prompt)` — one extra synchronous, read-only model call,
  returns its reply text for another actuator call to use (usually
  `actuator.notify`'s `body_md`):

  ```yaml
  on-enter: |
    actuator.notify('Nice!', actuator.prompt('Briefly acknowledge that state B was reached.'))
  ```

  System prompt: `general-prompt` + `prompt` (never the state's own
  `contextual-prompt`). Attachments: global (§2) + this action's
  on-enter-evaluation state's own (§5). Signal definitions and current
  `env:` are included as context; unlike a normal turn, no `[signals]`/
  `[env]`/`[audio]` reply is requested. History is the same real history a
  normal reply would see (subject to `history-cutoff`). Nothing is
  persisted, and it never updates `env`/evaluates a signal/fires a
  transition — read-only, like `send_mail`, but its return value is real
  text. This is what replaced the old, removed `action-prompt` field.

Every call's return value (if any) becomes wire-ready JS the frontend
runs verbatim, **except** `actuator.prompt(...)`'s — meant only for
another actuator call in the same line to consume (always wrap it, e.g.
in `actuator.notify(...)`; a bare `actuator.prompt(...)` line contributes
nothing). A call with nothing to tunnel (`send_mail`, `defer`)
contributes nothing either. Multiple lines concatenate in order.

`send_mail`/`defer`/`actuator.prompt(...)` never run during a test
replay/benchmark. A real side effect (`send_mail`, `defer`) also never
runs during EditProjectView's embedded "Test" chat unless that session's
"Run actuators" toggle is on — while off (default) it's suppressed and
reported back as a `notify(...)` toast describing what would have
happened. `celebrate`/`notify`/`prompt` have no real-world side effect to
suppress, so they always run.

## 7. Attachments

A filename under any `attachments:` (global, a signal's, or a state's —
**not** an action's, §6) must be present alongside `index.yml` in the same
upload — missing files fail validation by name.

| Extension | Sent as | Guaranteed to reach the model? |
| --- | --- | --- |
| `.yml`, `.yaml`, `.md`, `.txt`, `.csv` | Literal text, inlined into the prompt | **Yes** — every provider. |
| Anything else (e.g. `.pdf`, `.docx`) | Raw bytes, base64, `application/octet-stream` | **No** — provider-dependent (Gemini/OpenAI-driver providers drop it silently; Anthropic passes it through, not guaranteed useful). |

For anything that must reliably reach the model, use a text extension.
Every applicable attachment list is prepended as one synthetic (never
persisted) "user"/"assistant: Understood." exchange ahead of the real
conversation.

## 8. `init-action`

```yaml
init-action:
  target: lobby
  on-enter: actuator.celebrate()
```

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `target` | **yes** | string | Starting state — must be a real key under `states:`. |
| `on-enter` | no | string | Same mechanics as any action's (§6.4), sent alongside the first state, the one time init-action fires. |

A mapping, not a list item — otherwise a regular action with no
`name`/`ui-label`/`trigger`/`attachments` (fixed internally).

## 9. Validation checklist

Enforced at upload/boot — an invalid project is rejected outright,
nothing partially applied. Roughly in order of how you're likely to hit them:

- `project.id` (§2.1) present, a valid Python identifier — no dots/hyphens/spaces.
- `project.revision` (§2.1), if given, a non-negative integer.
- `states:` present, a mapping, no key `""`.
- `init-action` present, a mapping, non-empty `target` naming a real state.
- Every state entry is itself a mapping (a common mistake: `actions:`
  indented as a sibling of the state key instead of nested under it —
  YAML happily parses that as its own separate, invalid state).
- Every state has **exactly one** of `contextual-prompt` / `fixed-message`.
- Every state's `transition-log-level`, if given, is a valid level.
- Every action's `target` (incl. `init-action`'s) names a real state (or is a self-loop).
- Every action's `trigger`, if given: syntactically valid and every
  reference resolves (§6.2's rules per namespace).
- Every action's `env`, if given: a mapping, each expression validated the same way as `trigger`.
- Every action's `on-enter`, if given: one `actuator.<name>(...)` call per
  non-blank line, validated the same way plus its own argument-count check.
- No signal named after a reserved core metric (§3).
- Every `attachments:` entry (global/signal/state, not action) names a file actually present in the upload.
- A `.zip` upload: exactly one `index.yml` at the root, attachments flat.

## 10. Worked examples

**Minimal** (`Hello world.zip`):

```yaml
init-action:
  target: Hello

states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
```

**Signals/metrics/triggers**: `Metrics Playground.zip` (self-looping) and
`Metrics Playground (states).zip` (each trigger lands on its own final
state) — one signal + one action per core metric, with an extensive
comment block on exercising each from a running backend.

**Richer real-world examples**: `default/index.yml` (multiple signals with
attachments, a `fixed-message` state, per-state `transition-log-level`);
`Aprendr català/index.yml` (`history-cutoff`, a `fixed-message` state,
`on-enter: actuator.celebrate()`, `actuator.notify(...)`/`actuator.prompt(...)`
combos surfacing generated hints as toasts); `Drogodependencia/index.yml`
(simpler, neither).
