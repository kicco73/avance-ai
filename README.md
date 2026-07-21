# Avance — State Engine Prototype

Full-stack prototype of a conversational system driven by a deterministic finite
automaton (DFA) for an alcohol-related harm-reduction pathway (Prochaska &
DiClemente's TTM + Marlatt & Gordon's Relapse Prevention).

State transitions are triggered manually by the user via buttons, not inferred by
the AI. No authentication, no persistence: everything lives in memory and resets
on backend restart or on clicking "Reset".

## Starting the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

# open .env: choose LLM_PROVIDER (anthropic or gemini) and enter the matching API key
uvicorn main:app --reload
```

The backend starts on `http://localhost:8000`.

## Starting the frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend starts on `http://localhost:5173` (the backend only accepts CORS
requests from this origin).

## Usage

1. Open `http://localhost:5173`.
2. Chat freely in the central window: every message is sent to the backend over a
   websocket connection, which builds the system prompt by combining the current
   state's `contextual_prompt` with `general_instructions` (both read from
   `state_machine.yml`), and calls the configured LLM provider (see
   [Switching LLM provider](#switching-llm-provider)) with the full conversation
   history. If the provider reports a transient overload (HTTP 503), the backend
   retries automatically with exponential backoff (up to 5 retries) and pushes
   live status over the socket instead of the frontend polling for it; if all
   retries are exhausted, the failed message gets a resend icon so you can retry
   it without retyping.
3. When you judge that the conversation indicates a state change, click the
   corresponding button in the action bar: the transition is applied immediately
   and the state bar at the bottom updates. The chat is **not** touched by this
   action.
4. "Reset" clears state and conversation and returns everything to the initial
   state (`precontemplation`).

## Switching LLM provider

The backend abstracts the model call behind a common interface
(`backend/llm_provider.py`), with two interchangeable implementations in
`backend/providers/`: `anthropic_provider.py` (Claude) and `gemini_provider.py`
(Google Gemini).

To switch provider:

1. Change `LLM_PROVIDER` in `.env` (`anthropic` or `gemini`).
2. Make sure the matching pair of variables is set
   (`ANTHROPIC_API_KEY`/`CLAUDE_MODEL` or `GEMINI_API_KEY`/`GEMINI_MODEL` —
   see `.env.example`). To use a lighter/cheaper Gemini model such as
   `gemini-flash-lite-latest`, just set `GEMINI_MODEL` to it — it's the same
   API, only the model name changes.
3. Restart the backend.

No other change is needed: the provider is instantiated exactly once at server
startup, and if `LLM_PROVIDER` is unset or not a recognized value the server
fails to start, with an explicit error in the console.

The `crisis` state stays non-generative regardless of the selected provider
(see [Exception: the `crisis` state](#exception-the-crisis-state) below): the
model is still called, but only to translate a fixed message, never to
generate free-form content.

For Gemini's free tier, get a free `GEMINI_API_KEY` from
[Google AI Studio](https://aistudio.google.com/apikey) — no credit card
required.

## Editing the automaton

`backend/state_machine.yml` is the **single source of truth** for states, actions,
and contextual prompts. To add or modify a state:

1. Add/edit an entry under `states:` with `label`, `final`, `description`,
   `contextual_prompt`, and the list of `actions` (each with `name`, `label`,
   `button_text`, `target`).
2. Make sure every `target` referenced by an action matches an existing state
   key — the backend validates this constraint at startup and won't start if the
   YAML is inconsistent.
3. Restart the backend (`--reload` does this automatically when the file is
   saved).

No Python code needs to change for these edits.

### Exception: the `crisis` state

The `crisis` state (and any other state you mark the same way) is
**non-generative** for safety reasons: instead of `contextual_prompt`, its
YAML entry sets a `fixed_message` field. When the current state has one, the
backend never lets the model generate free-form content — it only asks it to
translate `fixed_message` verbatim into whatever language the user is
writing in, and returns that translation as the reply.

**Important**: the crisis resources in `crisis.fixed_message`
(`backend/state_machine.yml`) are a **prototype placeholder** (Spanish
emergency numbers used as an example) and are explicitly marked
`TO BE REPLACED`. Before any real-world use they must be replaced with
resources verified, up to date, and validated by a qualified clinical team
for the app's target territory/country.

## Project structure

```
avance-prototype/
├── backend/
│   ├── main.py                        # FastAPI entrypoint: REST endpoints + /ws/chat websocket
│   ├── automaton.py                   # YAML parsing + DFA logic
│   ├── llm_provider.py                # abstract interface shared by LLM providers
│   ├── providers/
│   │   ├── factory.py                 # selects the provider from LLM_PROVIDER
│   │   ├── anthropic_provider.py      # Anthropic API (Claude) call wrapper
│   │   └── gemini_provider.py         # Google Gemini API call wrapper
│   ├── state_machine.yml              # automaton definition + general_instructions + contextual/fixed prompts
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── components/
│   │   │   ├── ChatWindow.vue
│   │   │   ├── StateBar.vue
│   │   │   └── ActionButtons.vue
│   │   └── api.js                     # REST calls + /ws/chat websocket client
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## API endpoints

| Method | Path          | Description                                                             |
|--------|---------------|-------------------------------------------------------------------------|
| GET    | `/api/state`  | Current state, label, description, available actions                    |
| WS     | `/ws/chat`    | Chat channel — see below                                                |
| POST   | `/api/action` | `{action_name}` → applies the transition if valid for the current state |
| POST   | `/api/reset`  | Resets state and history to the initial condition                       |

`/ws/chat` replaces a plain request/response: the client sends `{message}`,
and the backend pushes one or more JSON frames back as the turn progresses —

- `{type: "retrying", attempt, max_attempts, retry_in}` — sent once per second
  while backing off after a transient (HTTP 503) provider failure; the client
  only renders these, it never decides whether/when to retry.
- `{type: "done", reply, state}` — the turn succeeded; `state` is the same
  shape as `GET /api/state`.
- `{type: "failed", error}` — non-retryable error, retries exhausted, or the
  current state is `final: true` (the model is never called for a message
  sent once the conversation has ended — see below).
- `{type: "error", error}` — rejected because a turn is already in flight on
  this connection (the server processes one message at a time).

A state with `final: true` is terminal: the frontend disables the chat input
once there (with an explanation, `Reset` stays available), and the backend
independently rejects any message that reaches it anyway — regardless of
whether the state was entered manually or via auto-tracking. Currently only
`crisis` is final; `maintenance` still has outgoing transitions (`relapse`,
`crisis`) so the conversation must stay open there.

## Known limitations of the prototype

- Single user, in-memory state: one backend process, no concurrency between
  sessions.
- No persistence: restarting the backend clears everything.
- Transitions are manual; automatic inference of transitions from the
  conversation is planned as a later phase, not implemented here.
- The `crisis` state's resources are placeholders (see above) and are not
  suitable for real-world use as they are.


DOCKER COMMANDS

docker build -t avance . ;
docker run --name avance-ai --env-file backend/.env -p 8080:80 avance