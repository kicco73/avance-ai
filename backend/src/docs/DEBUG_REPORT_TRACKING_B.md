# Debug report — Tracker BE, parte B

Prerequisite (Prompt 7 — `TagPromptBuilder`/`TurnProtocol.generate_reply_with_schema`) confirmed
already applied before starting (`tracking/tag_prompt_builder.py` exists, both
`TurnProtocolUsingSchema`/`TurnProcotolUsingTextExtraction` implement `generate_reply_with_schema`).

## 1. Regeneration without re-requesting signals

Wrote `backend/tests/test_regeneration_skips_signals.py`
(`test_regeneration_call_does_not_request_signals`): builds a real
`TrackingProcessorAfterUserMessage` against a schema-capable fake AI service that records every
`schema` dict it's called with (its keys are exactly the requested tags) and fires a
transition-triggering `signals` value on its first call only. Asserts the first call's schema
includes `"signals"`, the regeneration call's does not.

**The test failed against the current code**, confirming the waste described in the prompt: both
calls went through the same `self.generate_reply(state, ...)` → `build_turn_protocol()` →
`protocol.generate_reply(...)` path, so the regeneration call requested the exact same four tags
(`signals`, `audio`, `text`, `env`) as the first one.

**Fix applied**, exactly as specified:

- Added `TrackingProcessor._build_base_prompt_and_history(state) -> tuple[str, list[dict]]`
  (`tracking/tracking_processor.py`) — factored out of `generate_reply`'s own body (extracted the
  shared chat-history assembly into a small `_build_chat_history` helper too, to avoid duplicating
  it) so a subclass in another module can get the same `base_prompt`/`chat_history` `generate_reply`
  itself would build for a given state, without going through the name-mangled
  `__build_turn_prompt_parts` directly (private to `TrackingProcessor`, unreachable from
  `tracking_processor_user.py` by design). `generate_reply` itself is behaviorally unchanged — same
  inputs in, same `protocol.generate_reply(...)` call out.
- `tracking_processor_user.py`'s `_get_ai_reply`, regeneration branch only:
  ```python
  base_prompt, chat_history = self._build_base_prompt_and_history(self.out.state)
  async for chunk in self.build_turn_protocol().generate_reply_with_schema(
      base_prompt,
      tag_specs=[('audio', 'audio'), ('text', 'text'), ('env', 'env')],
      chat_history=chat_history,
      on_metadata=self.on_receiving_metadata_when_repeating_the_call,
  ):
      ...
  ```
- `on_receiving_metadata_when_repeating_the_call` no longer handles `'signals'` — confirmed dead
  (this method has no other call site in `backend/src/`) and removed, with a comment explaining why.

Note for whoever picks this up next: `generate_reply_with_schema` (per its own Prompt 7 design)
never injects the *current* stored env content into the prompt the way `generate_reply`'s
`env=env.serialise_as_text()` does — it only sends the `env` tag's generic instruction preamble.
The regeneration call still asks the model to *report* an env update (`'env'` stays in
`tag_specs`), just without first showing it the current env state. This is exactly what was
specified, not a deviation, but flagging it since it's a small loss of context on the regeneration
path specifically — a candidate for `tag_specs` to eventually carry per-tag content too, if that
turns out to matter in practice.

## Explicitly not touched

The first call (`self.generate_reply(self.user.state, self.on_receiving_metadata_that_may_trigger_status_change)`)
is untouched — still requests the full tag set, still the one that detects the transition. No early
stream abort was introduced during that first call. `TurnProtocolUsingSchema`/`TurnProcotolUsingTextExtraction`'s
`generate_reply`/`_generate_reply` are untouched from Prompt 7; only the regeneration branch's call
shape changed.

## Verification

- `test_regeneration_skips_signals.py`: 1/1 pass (fails against the pre-fix code, confirmed before
  applying the fix).
- Full suite: 76 failed / 424 passed (post Prompt 7) → **76 failed / 425 passed** (+1, the new
  test). Failing-test set byte-for-byte identical to before (`diff` of sorted `FAILED` lines empty)
  — no new failures, nothing incidentally fixed. Also re-ran
  `test_chat_service_evaluation_points.py`, `test_metrics_playground_sample.py`,
  `test_metrics_playground_states_sample.py`, `test_ai_provider_metadata_integration.py`,
  `test_metadata_handler_env.py` individually: all failures present are the already-documented
  `autotracking_on_user_message` fallout from Prompt 4 (`TEST_CLASSIFICATION.md`/
  `DEBUG_REPORT_TRACKING_A.md`), nothing new.
