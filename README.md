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
2. Chat freely in the central window: every message is sent to the backend, which
   builds the system prompt by combining the current state's `contextual_prompt`
   (read from `state_machine.yml`) with fixed general instructions, and calls the
   configured LLM provider (see [Switching LLM provider](#switching-llm-provider))
   with the full conversation history.
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
   see `.env.example`).
3. Restart the backend.

No other change is needed: the provider is instantiated exactly once at server
startup, and if `LLM_PROVIDER` is unset or not a recognized value the server
fails to start, with an explicit error in the console.

The `crisis` state remains handled outside both providers (see
[Exception: the `crisis` state](#exception-the-crisis-state) below): in that
state no model is ever called, regardless of the selected provider.

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

The `crisis` state is **hardcoded as non-generative** for safety reasons: while
the session is in this state, `POST /api/chat` never calls the model (whichever
LLM provider is configured) and always returns the same fixed message, defined
in the `CRISIS_FIXED_MESSAGE` constant in `backend/main.py`.

**Important**: the crisis resources included in `CRISIS_FIXED_MESSAGE` are a
**prototype placeholder** (Spanish emergency numbers used as an example) and are
explicitly marked in the code as `TO BE REPLACED`. Before any real-world use they
must be replaced with resources verified, up to date, and validated by a
qualified clinical team for the app's target territory/country.

If in the future you want to make this behavior generic too (e.g. a
`non_generative: true` flag in the YAML instead of the hardcoded `"crisis"` key),
it's a contained change in `backend/main.py`, but it was left explicit and
hardcoded at this stage specifically to make the safety override easy to spot in
review.

## Project structure

```
avance-prototype/
├── backend/
│   ├── main.py                        # FastAPI entrypoint + REST endpoints
│   ├── automaton.py                   # YAML parsing + DFA logic
│   ├── llm_provider.py                # abstract interface shared by LLM providers
│   ├── providers/
│   │   ├── factory.py                 # selects the provider from LLM_PROVIDER
│   │   ├── anthropic_provider.py      # Anthropic API (Claude) call wrapper
│   │   └── gemini_provider.py         # Google Gemini API call wrapper
│   ├── state_machine.yml              # automaton definition + contextual prompts
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── components/
│   │   │   ├── ChatWindow.vue
│   │   │   ├── StateBar.vue
│   │   │   └── ActionButtons.vue
│   │   └── api.js
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## API endpoints

| Method | Path          | Description                                                            |
|--------|---------------|--------------------------------------------------------------------------|
| GET    | `/api/state`  | Current state, label, description, available actions                    |
| POST   | `/api/chat`   | `{message}` → appends to history, calls the configured LLM provider, returns the reply |
| POST   | `/api/action` | `{action_name}` → applies the transition if valid for the current state  |
| POST   | `/api/reset`  | Resets state and history to the initial condition                        |

## Known limitations of the prototype

- Single user, in-memory state: one backend process, no concurrency between
  sessions.
- No persistence: restarting the backend clears everything.
- Transitions are manual; automatic inference of transitions from the
  conversation is planned as a later phase, not implemented here.
- The `crisis` state's resources are placeholders (see above) and are not
  suitable for real-world use as they are.
