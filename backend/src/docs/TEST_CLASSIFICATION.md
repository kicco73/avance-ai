# Test classification — `backend/tests/` (branch `mega-refactor`)

Every test in `backend/tests/` has been classified against the current architecture and marked
with exactly one pytest marker: `regression`, `contract`, or `needs_review` (registered in
`backend/pytest.ini`). This document is the baseline: subsequent debugging prompts should treat
the "raw suite outcome" section below as the starting point, and the marked tests themselves as
the reference for what "correct behavior" means today.

Markers:
- `@pytest.mark.regression` — verifies a specific, punctual behavior/fact continues to hold.
- `@pytest.mark.contract` — verifies an interface/structure (response shape, event order, method
  signature, data schema) the system must keep respecting.
- `@pytest.mark.needs_review` — type or current validity could not be determined with confidence.

No categorization scheme pre-existed in the project (no markers were registered before this
pass), so this one was introduced fresh rather than reusing an existing convention.

## 1. Counts

| | tests | of which invalid → action taken |
|---|---:|---|
| `regression` | 246 | 3 deleted (all in `test_turn_strategy_compute_explicitly.py`) |
| `contract` | 245 | 26 rewritten (5 files) |
| `needs_review` | 0 | — |
| **Total collected** | **491** | 29 tests changed/removed; 462 left as-is (just marked) |

Verified via `pytest -m regression --collect-only` → 246/491, `pytest -m contract --collect-only`
→ 245/491, `pytest -m needs_review --collect-only` → 0/491, and
`pytest -m "not regression and not contract and not needs_review" --collect-only` → 0/491 (every
test carries exactly one marker).

A prerequisite fix, not a classification action: **`backend/tests/conftest.py` was broken** before
this pass — its `app` fixture called `TrackingService(...)`/`ChatService(...)` with obsolete
constructor signatures (positional-argument order and keyword arguments from a previous
refactor iteration), which would have failed nearly every controller-level test at fixture setup,
independent of what those tests actually check. Fixed to match the current constructors
(`ChatService(db, ai_service, project_service, session_manager, tracking_service, metric_service)`,
`TrackingService(db, ai_service, project_service, metrics_service)`) before any test was evaluated.
Its `FakeAiService` was also missing `is_provider_with_schema()` (required unconditionally by
`tracking/tracking_processor.py`) and `generate_stream()` wasn't an actual async generator (no
`yield`) — both fixed for the same reason: test-infrastructure wiring, not application code, and
not something any individual test could reasonably be blamed for.

## 2. Deleted tests (regression + invalid)

All three are in **`backend/tests/test_turn_strategy_compute_explicitly.py`**, which tested a
`compute_explicitly` mechanism/API that no longer exists anywhere in `backend/src/` (only stale
docstring references remain):

1. `test_v1_compute_explicitly_degrades_to_empty_dict_on_ai_failure`
2. `test_v2_compute_explicitly_degrades_to_empty_dict_on_ai_failure`
   — Criterion: no `try`/`except` wraps the `ai_service` call anywhere in
   `backend/src/tracking/turn_protocol*.py` anymore. An `AIServiceError` now propagates uncaught to
   the centralized handler (`backend/src/error_handlers.py:41,58`), it is never swallowed into an
   empty dict at this layer. The behavior these tests pinned no longer exists at all, valid or not.
3. `test_v2_compute_explicitly_degrades_to_empty_dict_on_malformed_json`
   — Criterion: malformed-JSON tolerance now lives entirely inside `ai/ai_service.py`'s own
   response parser (~lines 122–168), unreachable from a fake that only exercises the
   `TurnProtocol` layer. Nothing at the layer this test targets could ever exhibit the behavior
   being asserted.

## 3. Rewritten tests (contract + invalid → rewritten to the current contract)

| File | Old contract | New contract |
|---|---|---|
| `test_ai_provider_metadata_integration.py` (2 tests, 1 collapsed from 2) | `strategy.generate_reply(...)` awaited and returned a `(reply, audio_text, signal_values, env_updates)` tuple; separate blocking vs. streaming call shapes. | `TurnProtocol.generate_reply(base_prompt, signal_definition, env, chat_history, on_metadata) -> AsyncIterator[str]` — always streaming; metadata delivered live via a synchronous `on_metadata(key, value)` callback with raw string values. Blocking mode doesn't exist, so the old 2-test (blocking/streaming) split collapsed into 1 parametrized test. |
| `test_auto_tracker_action_env.py` (6 tests) | Action-triggered `env:` persistence driven through `AutoTracker.run()`. | `tracking.auto_tracker.AutoTracker` is deleted entirely (ground-truth table row #5). Same behavior (`tracking/tracking_processor.py`'s `_apply_action_env`) now driven through `TrackingService.process()` against fake project/AI services. |
| `test_auto_tracker_metrics.py` (5 tests) | Metric-only/metric+signal trigger firing driven through `AutoTracker.run()`. | Same behavior, driven through `TrackingService.process()`. `MetricService.merge_if_referenced`'s skip-when-unreferenced optimization is unchanged, only the entry point moved. |
| `test_metadata_handler_env.py` (8 tests) | `MetadataHandler._parse_env_tag` (private) and `MetadataHandler.build_prompt`. | `_parse_env_tag` → public `parse_raw_env` (5 tests renamed, same assertions). `build_prompt` no longer exists on `MetadataHandler` at all — prompt assembly moved to `TurnProtocol.__build_prompt`; the 2 tests covering it were rewritten against a recording `TurnProtocol` subclass that captures the built prompt. |
| `test_turn_strategy_compute_explicitly.py` (5 of 8 tests; 3 deleted, see §2) | `compute_explicitly`-specific extraction API. | Rewritten against `TurnProtocol.generate_reply`'s `on_metadata` contract, using fakes matching the current `ai_service` interface. One test flipped from "ignores audio/env" to `test_v2_generate_reply_also_reports_audio_and_env_under_their_own_keys`, since the current contract forwards every metadata key, not just `signals`. |

Two additional **mechanical, test-side-only** fixes (not contract rewrites — the assertions
didn't change, only how the test reaches the current API):
- `test_chat_service_sessions.py`: stale `TrackingService(...)` call using removed
  `get_active_automaton`/`get_username`/`get_active_project_name` kwargs → current `project_service`
  kwarg.
- `test_controller_sessions.py::test_sessions_list_reflects_has_annotations_per_session`:
  `turn["reply"][0]["id"]` → `turn["assistant_message_id"]`, since `process_turn`'s `"reply"` key
  (`self.out.messages`) is never populated by either `TrackingProcessorAfterUserMessage` or
  `TrackingProcessorAfterAiMessage` — always `[]`. The message id was already available via the
  documented top-level `assistant_message_id` key (ground-truth table row #6, `process_turn`
  returns a dict).

## 4. `needs_review`

None. Every test's classification (regression vs. contract) and current validity could be
determined with confidence against the actual current source.

## 5. Per-layer breakdown (as classified)

| Layer (files) | Tests | regression | contract | Notes |
|---|---:|---:|---:|---|
| Automaton / config (10 files: `test_automaton_*`, `test_config.py`, `test_signal*.py`) | 93 | 0 | 93 | All already valid; module-level `contract` markers (schema/interface tests throughout — builder validation, trigger-name resolution, `Automaton`'s stateless API). |
| Tracking / chat-turn (10 files: `test_ai_provider_metadata_integration.py`, `test_auto_tracker_*.py`, `test_chat_*.py`, `test_metadata_handler_env.py`, `test_text_filter.py`, `test_turn_strategy_compute_explicitly.py`) | 80 | 53 | 27 | See §2/§3 for the invalid ones. `AutoTracker` deletion (table row #5) is the dominant source of invalidation here. |
| DB layer (9 files: `test_db_*.py`) | 106 | 89 | 17 | Below the refactor's changed-behavior surface (table rows are all automaton/tracking/project/config); nothing invalidated. |
| Controller pt.1 + session manager (8 files: `test_controller_action_on_enter.py`, `test_controller_backup.py`, `test_controller_benchmark.py`, `test_controller_chat_truncate.py`, `test_controller_docs.py`, `test_controller_env.py`, `test_controller_metrics.py`, `test_session_manager.py`) | 77 | 11 | 66 | All valid; conftest.py `FakeAiService` fix (see §1) unblocked most of this batch's real runs. |
| Controller pt.2 — project management (7 files: `test_controller_project_*.py`, `test_controller_projects.py`, `test_controller_sessions.py`, `test_controller_triggers_metrics.py`) | 44 | 33 | 11 | All valid (`ProjectService` statelessness, table row #9, holds throughout). One mechanical wiring fix, see §3. |
| Metrics framework (8 files: `test_benchmark_calculator_integration.py`, `test_metric_service.py`, `test_metrics_*.py`) | 86 | 45 | 41 | Below the refactor's changed-behavior surface; nothing invalidated. |

(Per-layer subtotals above are as reported by each reviewer and may be off by a handful of tests
from double-counting parametrized cases — the authoritative totals are the ones in §1, taken
directly from `pytest --collect-only`.)

## 6. Raw suite outcome (baseline for future debugging)

```
$ pytest -q
...
37 failed, 454 passed, 5 warnings in 25.85s
```

By marker: `-m regression` → 25 failed, 221 passed (246 total). `-m contract` → 12 failed,
233 passed (245 total). `-m needs_review` → 0 (none exist).

Every one of the 37 failures traces to one of five real bugs in `backend/src/` (not test bugs —
left failing per instructions, since a failing regression/contract test whose underlying contract
is still valid is exactly the material later debugging prompts should use). Application code was
**not** modified to fix any of these.

1. **`backend/src/db/utils.py:4-6`** — `_utc_iso()` is stubbed to `return 'c'`, with the real
   `dt.replace(tzinfo=timezone.utc).isoformat()` implementation commented out below it. Breaks
   every downstream `pandas.to_datetime` call over message/signal timestamps. Root cause of 30 of
   the 37 failures, across `test_auto_tracker_action_env.py`, `test_auto_tracker_metrics.py`,
   `test_benchmark_calculator_integration.py`, `test_controller_benchmark.py`,
   `test_controller_chat_truncate.py`, `test_controller_env.py`, `test_controller_metrics.py`,
   `test_metrics_playground_sample.py`, `test_metrics_playground_states_sample.py`.
2. **`backend/src/error_handlers.py:38`** — an already-interpolated f-string is passed as a
   logger format string alongside extra positional args (`logger.exception(f"...", method, path)`),
   raising `TypeError: not all arguments converted during string formatting` while handling
   another exception, masking the real error. Surfaces in `test_controller_projects.py`.
3. **`backend/src/project/project_service.py:354`** (`list_projects`) — calls
   `self.get_active_project_name()` unguarded, which raises `FileNotFoundError` (by its own
   docstring's design) whenever nothing is active; `GET /api/projects` 500s instead of returning
   `active: None`. 3 failures in `test_controller_projects.py`.
4. **`backend/src/chat/chat_service.py:470-478`** (`process_turn`, used by
   `POST /api/chat/messages`) — never calls `_require_active_session`, unlike
   `apply_manual_action` (line 452); a superseded/closed session's text messages aren't rejected
   with 409 as `_require_active_session`'s own docstring promises. 2 failures in
   `test_controller_sessions.py`.
5. **`backend/src/tracking/text_filter.py` / `turn_protocol_using_text_extraction.py`** — the v1
   (non-schema) path's per-tag `on_metadata` callbacks are wrapped in
   `asyncio.create_task(...)`, but the wired-up callback in this path is a plain sync function, not
   a coroutine → `TypeError: a coroutine was expected, got None` the moment any tag closes. A
   separate closure late-binding bug also present (every callback reports under the last tag's
   name). 2 failures: `test_ai_provider_metadata_integration.py` (v1 case) and
   `test_turn_strategy_compute_explicitly.py` (v1 case).

30 (bug 1) + 1 (bug 2, indirectly — see `test_controller_projects.py` note) + 3 (bug 3) + 2 (bug 4)
+ 2 (bug 5) accounts for all 37 failures. (Bug 2 is the exception-handling path that all three
`test_controller_projects.py` failures pass through; bug 3 is the actual root cause of those same
3 — bug 2 just makes the resulting error response malformed on top of the underlying 500.)
