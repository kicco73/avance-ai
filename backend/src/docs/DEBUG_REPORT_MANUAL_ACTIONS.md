# Debug report — manual actions (`POST /api/action`)

## Baseline

Ran the `regression` (and `contract`, for context) tests from `TEST_CLASSIFICATION.md` that cover
manual actions/transitions: `test_controller_action_on_enter.py`, `test_controller_chat_truncate.py`,
`test_chat_service_manual_action_env.py`, `test_controller_project_undo_redo.py`,
`test_chat_service_evaluation_points.py`, `test_controller_project_edit_preserves_chat.py`,
`test_metrics_playground_states_sample.py`, `test_metrics_playground_sample.py`,
`test_automaton_eval_action_env.py`, `test_db_action_env.py` — all passed before this fix. That's
expected: every one of them fires a manual action against a session with **no prior message
history**, which turns out to matter (see below).

## 1. Reproduction and localization

Traced the full chain per the request: `POST /api/action` → `ChatService.apply_manual_action()` →
`ProjectService.apply_manual_action()` (`automaton.move()`, `db.save_transition()`) →
`ChatService._apply_action_env()` → `ChatService._messages_for_transition()` → response.

The existing unit/integration tests all pass because they fire the action on a session with **no
messages yet**. Real usage (and the UI) fires actions on sessions with real conversation history,
which none of the existing tests exercised for the `env:`-bearing-action path specifically. Built a
standalone repro (custom minimal project with a state `a`, an action `advance` carrying `env: {reset_counter: "True"}`,
target `b`) driving the full FastAPI stack via `TestClient`, exactly as the manual-action-button
flow does:

1. Activate the project, bootstrap a session (`GET /api/chat/session`).
2. Send one real chat message (`POST /api/chat/messages`) — creates real, DB-persisted message rows
   with real timestamps.
3. Fire the manual action (`POST /api/action`, `action_name: "advance"`).

Step 3 broke: **`POST /api/action` returned `400 {"error": {"message": "Unknown datetime string
format, unable to parse: c"}}`** — the cycle never reached the response-building step.

The break is in **`ChatService._apply_action_env()`**
(`backend/src/chat/chat_service.py:436-442`): because the action has an `env:` field, it calls
`self.metric_service.calculate_values()` to build the scope those `env:` expressions evaluate
against. That call builds a `UserAnalyticsData` over the session's real messages
(`metrics_framework/timeline.py`'s `UserAnalyticsDataBuilder._load_messages`), which does
`pd.to_datetime(frame["timestamp"], utc=True)` on the messages' timestamp column — and every
message's timestamp string was the literal `"c"`.

**Root cause: `backend/src/db/utils.py`'s `_utc_iso()`** was stubbed:
```python
def _utc_iso(dt: datetime) -> str:
	return 'c'
    #return dt.replace(tzinfo=timezone.utc).isoformat()
```
with the real implementation commented out beneath the stub. `_utc_iso` is the single place every
DB-backed timestamp (messages, sessions, tracking rows) gets serialized to a string throughout the
app (`db/messages.py`, `db/tracking.py`, `db/sessions.py` via `chat_service.py`, `tracking/env.py`),
so this one stub poisoned every downstream consumer that ever parses a real timestamp — pandas in
this case. This is the exact same bug already documented as bug #1 in `TEST_CLASSIFICATION.md`
(found there via other symptoms: benchmark/metrics endpoints, playground samples). It only breaks
the *manual-action* cycle specifically for an action that carries an `env:` field, once the session
already has real messages — which is why none of the existing `env:`-focused unit tests (all built
on empty-history fixtures) ever hit it, and why my first repro attempt (an action with no `env:`)
also didn't reproduce anything.

## 2. Fix

`backend/src/db/utils.py`:
```python
def _utc_iso(dt: datetime) -> str:
	return dt.replace(tzinfo=timezone.utc).isoformat()
```
(removed the `'c'` stub and the commented-out line, restored the real implementation verbatim — no
behavior change beyond making it actually work).

Checked before applying: grepped the whole test suite for anything asserting the literal `'c'`
placeholder — none exists; every fixture that needs a real timestamp string builds its own via a
proper ISO helper, never by relying on `_utc_iso`'s broken output.

## 3. Verification

- Repro script re-run: `POST /api/action` now returns `200`, full response shape intact
  (`state`, `reply`, `on-enter`, `ai_model`, `session_id`), env update persisted, and the
  destination state's opening message generated correctly.
- Manual-action-relevant `regression`/`contract` tests (10 files, 68 tests): **68 passed**, no
  regressions.
- Full suite: **37 failed → 15 failed** (476 passed, up from 454). All 15 remaining failures are
  pre-existing, already-documented, unrelated bugs from `TEST_CLASSIFICATION.md` (#2
  `error_handlers.py` format-string bug, #3 `ProjectService.list_projects` unguarded
  `FileNotFoundError`, #4 `ChatService.process_turn` missing `_require_active_session`, #5
  `text_filter.py`'s v1 `asyncio.create_task` bug) — none of them touch the manual-action cycle,
  and none were introduced by this change.

No test was modified to make this pass — the fix was entirely in `backend/src/db/utils.py`.
