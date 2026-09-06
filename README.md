# Avance — State Engine Prototype

Full-stack prototype of a conversational system driven by a finite-state
automaton ("project"): each state carries its own prompt for the LLM, and
actions move the conversation between states — either manually (a button in
the UI) or automatically, when the model's own assessment of the
conversation (a set of numeric "signals") crosses a threshold declared on
an action ("trigger").

A project is just a `.zip` (an `index.yml` plus optional attachment files);
the format itself — states, actions, signals, triggers, attachments — is
documented separately in [`backend/src/docs/PROJECT_SPECS.md`](backend/src/docs/PROJECT_SPECS.md),
not here. This file covers the technical solution and how to install and
configure it.

## Architecture at a glance

```text
frontend/   Vue 3 SPA (Vite) — chat window, session panel, and an "Edit
            project" view with a YAML editor, a state-graph inspector, and
            live Signals/Metrics tabs.
backend/
  src/      FastAPI app.
    main.py                 entrypoint: wires everything below, exposes
                             the REST API and a shared /ws/notifications
                             socket (chat turns still go over REST/SSE)
    controller.py            composition root — merges the 4 screen-scoped
                             controllers below onto one router
    controllers/             every REST route, one file per FE screen
                             (chat/edit_project/label_project/settings)
    chat/                    turn processing, auto-tracking, sessions
    automaton/                index.yml parsing + the DFA itself
    project/                  project CRUD/activation/validation
    ai/, talk/, listen/       LLM / text-to-speech / speech-to-text providers
    metrics_framework/       domain-agnostic analytics over the stored
                             conversation history (engagement, retention,
                             activity consistency, state/signal stability)
    db.py                    single point of DB access (Peewee/SQLite)
  samples/  example projects for local development — see below
```

**Storage**: everything is a single SQLite database (`database.url` in
`.config.yml`) — chat sessions, messages, signal/transition history, and
every uploaded **project itself** (as a zip blob, keyed by name). There is
no `projects/` directory on disk: a project only exists once it has been
uploaded through the API/UI, or restored from a backup. `backend/samples/`
is not read by the running app at all — it's example content you upload
yourself to get started (see below), and fixtures used by the backend's
own test suite.

**Users**: single-user prototype, no authentication (`session.py`'s
`Session()` is a process-wide placeholder). No concurrency between
conversations beyond what `ChatSessionManager` does within that one user
(see its module docstring for the "open" vs "active" session distinction).

## Starting the backend

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd src
cp .config.example.yml .config.yml   # then edit it — see "Configuring" below
uvicorn main:app --reload
```

The backend starts on `http://localhost:8000`. If `.config.yml` is missing
or invalid, the process still starts (so the frontend gets a clear error
instead of a connection failure) but every request fails with a
"backend is not configured correctly" response — check the console log for
the actual `ConfigError`.

## Starting the frontend

```bash
cp frontend/.env.dev frontend/.env
cd frontend
npm install
npm run dev
```

The frontend starts on `http://localhost:5173` (the backend only accepts
CORS requests from this origin — see `main.py`).

## Getting started

A fresh database has no project in it. Open the app, use the **"Projects"**
menu's **Upload...**, and pick one of the examples under `backend/samples/`
(e.g. `Hello world.zip`, `Metrics Playground.zip`) — that also activates it.
From there:

1. Chat freely in the central window.
2. If the active project declares manual actions, use the buttons in the
   action bar to move between states yourself; actions with a `trigger`
   can also fire on their own once the referenced signal(s)/metric(s)
   cross their threshold (see auto-tracking below).
3. **"Reset"** clears your own conversation history for the active project
   and starts a fresh session at its initial state.
4. **"Edit project"** opens a YAML editor plus an Inspector: the state
   graph, the project's declared Signals (with live values), the core
   Metrics (see `metrics_framework/`), and the AI model currently in
   effect.

## Configuring (`.config.yml`)

Copied from `backend/src/.config.example.yml`, gitignored (it holds
secrets). Top-level sections:

- **`database.url`** — a Peewee connection URL. `sqlite:///avance.db` by
  default; anything else requires adding the matching driver to
  `requirements.txt` (not included).
- **`database.migration-strategy`** — optional, defaults to `stop`.
  What to do at startup when the on-disk schema doesn't match what the
  code expects: `stop` refuses to start and leaves the database
  untouched; `upgrade` migrates the schema in place (tables and columns
  added/removed incrementally, everything else preserved), refusing to
  start like `stop` if the difference can't be expressed that way;
  `drop` recreates every table from scratch, losing all data. Both
  `upgrade` and `drop` first save an automatic timestamped backup copy
  of the database file next to the original.
- **`chat-service.input-token-budget-per-turn`** — optional, defaults
  to `16000`. Caps how much of a session's own message history a turn's
  prompt can carry: walking backward from the latest message, as many
  messages as fit within this many cumulative tokens (`Message.tokens`)
  are sent to the model. Combined with a project state's own
  `history-cutoff` (see `PROJECT_SPECS.md` §5) in a single query
  (`Db.get_turn_history`) rather than replacing it — whichever cutoff is
  tighter wins.
- **`chat-service.total-token-budget-per-session`** — optional, defaults
  to `200000`. Display-only: exposed read-only to the frontend via
  `GET /api/state`'s own `total_token_budget_per_session`, so "Label
  sessions"' own session detail panel can show a burnt-vs-budget bar
  (input tokens summed across the session's `user` messages) with the
  exact numbers on hover. Nothing in the backend trims history against it.
- **`ai-service.providers`** — a non-empty, ordered list, backing *two*
  independent cascades — live chat and the test panel/batch runs (see
  `AiService.for_live`/`for_test` in `ai/ai_service.py`) — rather than
  one shared one. Within whichever cascade an entry belongs to, order is
  fallback order: the first entry is used; later ones only kick in if an
  earlier one becomes unavailable (rate limit, quota, outage — see
  `ai/cascading_llm_provider.py`). `driver` is one of `anthropic`,
  `gemini`, `openai` (the `openai` driver also accepts any
  OpenAI-compatible endpoint via `url`, e.g. a local llama.cpp server).
  `model` and `key` are provider-specific; `ui-label`/`ui-description`
  (both optional) are what the frontend's model-selector menu shows.
  `modes` (optional, e.g. `[live]` or `[test]`) — which cascade(s) this
  entry belongs to; omitted defaults to both, while an explicit empty
  list excludes it from both. At least one entry must remain in each
  cascade.
- **`talk-service`** / **`listen-service`** — optional (`enabled: false`
  or the whole section omitted by default). Same `providers` list shape
  as `ai-service`, but independent rosters, for text-to-speech and
  speech-to-text respectively. `listen-service`'s `faster-whisper` driver
  is fully local (no `key`, downloads/caches its own model on first use).
  When disabled, the corresponding endpoints respond with a clear
  "service not available" error instead of the backend failing to boot.
- **`project-service.invite-valid-days`** / **`project-service.invite-max-shares`**
  — optional, default to `7` and `3`. Govern "share project" invite
  links (Manage projects' own Share dialog, backed by
  `project/invites.py`'s `InviteManager`): how many days a freshly
  generated invite code stays redeemable, and how many new registrations
  it can carry before self-registration through it is refused —
  registration is invite-only (see `AuthService.complete_registration`).
  A fresh `Invite` row (its own code/expiry/budget) is created every
  time the dialog opens, never reused.

Restart the backend after any change (`--reload` does this automatically
when `.config.yml`'s containing files change, but `.config.yml` itself is
only read once at process start).

## What the API covers

The full, authoritative route list is `backend/src/controllers/` (one file
per FE screen) — grouped here by area. Every endpoint scoped to a project
or a session takes that id as a URL path segment, never a query param or
request-body field:

| Area | Examples |
| --- | --- |
| Chat | `GET/POST /api/chat/session(s)`, `DELETE /api/chat/sessions/{id}`, `GET /api/chat/sessions/{id}/messages`, `POST /api/chat/sessions/{id}/action`, `POST /api/chat/reset`. Sending a message is **not** an endpoint: a turn travels as a `turn` frame on the `/ws/notifications` websocket, the chat's only transport (see `backend/src/docs/PROJECT_SPECS.md` §0) |
| Auto-tracking | `GET/POST /api/chat/sessions/{id}/autotracking` — "Dev mode: freeze automatic state transitions", scoped to one 'test' session (EditProjectView.vue's own embedded "Test" chat); a native/imported session is always auto-tracked |
| Live analytics | `GET /api/chat/signals` (last computed signal values, active project), `GET /api/projects/{project_id}/metrics` (metrics_framework, computed on demand), `POST /api/triggers/preview` |
| AI model | `GET /api/ai/models`, `POST /api/ai/models/selection` |
| Voice | `GET /api/chat/messages/{id}/audio` (TTS), `POST /api/listen/transcribe` (STT) |
| Projects | `GET/POST /api/projects`, `POST /api/projects/upload` (create or add a revision — see `backend/src/docs/PROJECT_SPECS.md` §2.2), `PUT /api/projects/{project_id}/activate`, `GET/DELETE /api/projects/{project_id}`, `GET /api/projects/{project_id}/graph`, `GET /api/projects/{project_id}/signals`, `GET /api/projects/{project_id}/sessions`, `GET /api/projects/{project_id}/identifiers` |
| Project files | `GET /api/projects/{project_id}/files(/{file})`, `PUT/DELETE /api/projects/{project_id}/files/{file}` — the "Edit project" view's file explorer |
| Settings | `GET/POST /api/settings/backup` (the whole database, every project and every user's sessions, as a single restorable `.sqlite` file), `GET /api/settings/projects/runtime-status` |
| Status | `GET /api/state` |
| Notifications | `WS /ws/notifications` — one shared, always-on push channel per logged-in user (on-enter notifications, test-run progress, project health, cross-project wake-ups); chat turns themselves still go over REST/SSE above |

Every error response shares one shape, `{"error": {"message", "detail"}}`
(see `error_handlers.py`).

## Auto-tracking, signals and metrics

A project's `index.yml` can declare **signals** — numeric values the model
itself estimates from the conversation on each turn (auto-tracking) — and
give any action a **trigger**, an expression over those signals (evaluated
via `simpleeval`). Separately, `metrics_framework/` computes five
domain-agnostic metrics from the stored history (engagement, retention,
activity consistency, state stability, signal stability; see its own
README) — a trigger may reference either a declared signal or one of
these metric names, or combine both in the same expression. Metric names
are reserved: a project can't declare a signal that shadows one.

None of this — signal/state/action/trigger syntax — is described here; see
[`backend/src/docs/PROJECT_SPECS.md`](backend/src/docs/PROJECT_SPECS.md) for the complete
project-format specification, and `backend/samples/` for worked examples
(including `Metrics Playground.zip` and its state-based sibling
`Metrics Playground (states).zip`, both built specifically to exercise
trigger expressions over every metric).

## Docker

```bash
docker build -t avance .
docker run --name avance-ai -v $(pwd)/backend/src/.config.yml:/app/backend/src/.config.yml -p 8080:80 avance
```

Single container: `nginx` serves the built frontend and proxies to
`uvicorn` (see `Dockerfile`/`nginx.conf`), which listens on `:8000`
internally. `.config.yml` is mounted in, never baked into the image.

## Known limitations of the prototype

- Single user, single process: no real authentication, no concurrency
  model beyond one user's own sessions.
- Everything lives in one SQLite file; switching to another database
  engine requires adding its Peewee driver yourself.
