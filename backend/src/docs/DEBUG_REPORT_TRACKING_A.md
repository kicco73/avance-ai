# Debug report — Tracker BE, parte A

## Baseline

Ran the `regression`/`contract` tests from `TEST_CLASSIFICATION.md` touching
`chat/tracking_processor*.py`, `chat/chat_service.py`, `chat/turn_protocol*.py`,
`automaton/automaton*.py` before starting: 15 failing (the pre-existing bugs already documented
in `TEST_CLASSIFICATION.md`/`DEBUG_REPORT_MANUAL_ACTIONS.md`), 476 passing.

## 1. Static binding of `TrackingProcessor`

**Premise checked, found false — no bug, no fix made.** `ChatService` is indeed a singleton
(`main.py` constructs it once), but `ChatService.__init__` (`chat/chat_service.py`) never
references `TrackingProcessorAfterUserMessage`/`TrackingProcessorAfterAiMessage` at all — it has
no involvement in that choice. The selection happens entirely inside
**`TrackingService.process()`** (`tracking/tracking_service.py`), which is called fresh on every
turn and, at the top of that same call, re-resolves the automaton via
`self._project_service.get_active_automaton_and_state()` (line 184) — reading straight from the
DB's "active project" pointer, never cached — before branching on
`automaton.autotracking_on_ai_message` (line 193, post-point-2) a few lines later. Switching the
active project mid-session is picked up correctly on the very next turn; there is nothing fixed at
construction time to go stale. Confirmed no other layer caches this decision either (`TrackingService.automaton`
is a `@property` that also re-resolves on every access, never memoized).

## 2. Elimination of `autotracking_on_user_message`

Removed the flag and its YAML key everywhere, replacing every read with `not autotracking_on_ai_message`:

- `automaton/automaton.py`: dropped the `autotracking_on_user_message` constructor parameter and
  attribute from `Automaton`.
- `automaton/automaton_builder.py`: dropped parsing of `signal-tracking-on-user-message`; no longer
  passed to `Automaton(...)`.
- `tracking/tracking_service.py:193`: `if automaton.autotracking_on_user_message:` →
  `if not automaton.autotracking_on_ai_message:`.
- `tracking/tracking_processor.py:128`: `self.user.automaton.autotracking_on_user_message` →
  `not self.user.automaton.autotracking_on_ai_message`.
- `backend/samples/Aprendr català/index.yml` (+ rebuilt `.zip`, the two are kept in sync by hand
  per `PROJECT_SPECS.md`): removed the now-meaningless `signal-tracking-on-user-message: false`
  line. No behavior change — `signal-tracking-on-ai-message: true` alone already fully determines
  "after AI message" mode under the new single-flag rule, same mode this project used before.
- `backend/src/docs/PROJECT_SPECS.md` §2 and §4.1: updated the reference doc to describe the single
  `signal-tracking-on-ai-message` flag and its two exclusive modes, rather than two independent
  keys.

**Fallout — expected, not fixed (per instructions: a deliberate contract change, not a bug):**
removing the constructor parameter breaks every test that still constructs `Automaton(...)` with
`autotracking_on_user_message=...` — a `TypeError: unexpected keyword argument`, not an assertion
failure. This is **63 tests across 10 files**, all newly failing:
`test_ai_provider_metadata_integration.py`, `test_auto_tracker_action_env.py`,
`test_auto_tracker_metrics.py`, `test_automaton_triggerable_signal_names.py`,
`test_automaton_triggers_reference.py`, `test_chat_env.py`, `test_chat_service_evaluation_points.py`,
`test_chat_service_manual_action_env.py`, `test_metric_service.py`, `test_signal_evaluator.py`,
`test_signals_get_definition.py`. None of these are "test palesemente sbagliato" in the sense of
having been wrong before — they were valid, current contract tests as of `TEST_CLASSIFICATION.md`;
this prompt's own instruction deliberately obsoletes the constructor shape they assert. Left
untouched, not fixed, per the explicit "never touch the test" constraint. They'll need a follow-up
classification/rewrite pass (out of scope here) to drop the removed keyword everywhere it's still
passed.

## 3. Metadata "before" mode — channel order

Wrote `backend/tests/test_metadata_channel_order.py`, pinning `signals → audio → text → env` for
both v1 (`TurnProcotolUsingTextExtraction`) and v2 (`TurnProtocolUsingSchema`) when
`evaluate_signals_first=True`. "text" is never itself an `on_metadata` event in either
implementation (v1: no `[text]` tag is ever emitted; v2: `AiService.generate_stream_with_metadata`
explicitly excludes `"text"` from `on_metadata`, `ai/ai_service.py:148`) — its place in the sequence
is instead the moment the first visible reply chunk is yielded, which the tests log as a synthetic
`"text"` event alongside the real metadata ones.

**The v1 tests revealed two real bugs, both fixed** (both were preventing any correct/observable
ordering at all — not something an "order" fix in `turn_protocol*.py`'s own tag-sequencing logic
could touch, since neither test file nor `include_tags` was misordered):

- `tracking/text_filter.py`'s `StreamingTagFilter._process_tag_content` called
  `asyncio.create_task(self.on_tag(self.tag_content))` — but `on_tag` is a plain **synchronous**
  callable everywhere it's actually wired up (see `tracking/tracking_service.py`'s
  `on_metadata_sync_to_async`, which is exactly a sync-callable wrapper around the real async
  callback), so `self.on_tag(...)` returns `None`, and `asyncio.create_task(None)` raised
  `TypeError: a coroutine was expected, got None` the instant any tag closed. Fixed by calling
  `self.on_tag(self.tag_content)` directly, no `asyncio.create_task` wrapper. (Also dropped the
  now-unused `import asyncio`.)
- `tracking/turn_protocol_using_text_extraction.py`'s `_generate_reply` built
  `metadata_handlers = {tag: lambda value: on_metadata(tag, value) for tag in self.include_tags}`
  — classic closure late-binding: every one of those lambdas captured the same `tag` variable by
  reference, so no matter which tag actually closed, the callback that fired reported it under
  whichever tag was *last* in `self.include_tags`. Fixed with the standard default-argument
  binding: `lambda value, tag=tag: on_metadata(tag, value)`. Added
  `test_v1_metadata_events_report_under_their_own_tag_name` as a direct regression test for this
  (asserts `[audio]a[/audio][signals]s[/signals][env]e[/env]` reports
  `{"audio": "a", "signals": "s", "env": "e"}`, not all three collapsed onto `"env"`).

With both fixed, all "before"-mode order tests (v1 and v2) pass. `include_tags`'s own ordering
logic in `turn_protocol.py` (`('signals', 'audio', 'text', 'env')` when
`evaluate_signals_first=True`) was already correct — the bug was entirely in *reporting*, not in
the intended order itself.

## 4. Metadata "after" mode — channel order

Same test file, same two implementations, `evaluate_signals_first=False`: pinned
`audio → text → signals → env`. No further bug found — both v1 and v2 report this order correctly
once the two fixes from §3 are in place (they're shared code paths, not mode-specific).

## Explicitly not touched

Per instructions, `TrackingProcessorAfterUserMessage._get_ai_reply`'s early-transition
regeneration logic was not touched beyond the point-2 flag rename (which doesn't reach this file at
all — `tracking_processor_user.py` never reads `autotracking_on_user_message`/`autotracking_on_ai_message`
directly, only via `TrackingProcessor.build_turn_protocol()`/`TrackingService.process()`, both
already covered above).

## Verification

- `test_metadata_channel_order.py`: 5/5 pass.
- Full suite: 78 failed/413 passed (post-point-2, pre-points-3/4) → **76 failed / 420 passed**
  (420 = 413 + 5 new tests + 2 fixed). The 2 fixed: `test_ai_provider_metadata_integration.py`'s
  `[v1]` case and `test_turn_strategy_compute_explicitly.py`'s v1 case, both previously failing on
  the `asyncio.create_task` crash (documented as bug #5 in `TEST_CLASSIFICATION.md`) — now genuinely
  fixed, not just newly passing by accident.
- Remaining 76 failures: 13 pre-existing unrelated bugs (already documented) + 63 from the
  deliberate point-2 API change (see §2 above, left as-is per instructions).
