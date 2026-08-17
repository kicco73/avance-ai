# Debug report — consolidation of `_apply_action_env`

Prerequisite (Prompt 10 — `TrackingEngine`) confirmed already applied before starting.

## 1. Comparison

`ChatService._apply_action_env` (`chat/chat_service.py`, manual-action path) and
`TrackingEngine._apply_action_env` (`tracking/tracking_engine.py`, auto-tracking path — itself the
extracted body of `tracking_processor.py`'s former `_apply_action_env`) were byte-for-byte
identical in behavior:

```python
if not action.env:
	return
scope = {**signal_values, **<metrics>.calculate_values(), **<env>.to_dict()}
updates = automaton.eval_action_env(action, scope)
if updates:
	<env>.update_action_set(updates)
```

Same `scope` computation (same three sources, same merge order), same `automaton.eval_action_env`
call, same `update_action_set` call — the only difference was attribute names
(`self.metric_service`/`self.env` vs `self._metrics`/`self._env`), both referring to the exact
same kind of object (`MetricService`/`Env`) either way. **They coincide — consolidated.**

## 2. Consolidation

- `TrackingEngine._apply_action_env` → made public: `apply_action_env` (renamed, docstring updated
  to note it's now shared by both callers; `apply_transition`'s own internal call site updated to
  match).
- `ChatService`: added `self._tracking_engine = TrackingEngine(DbTrackingSink(db), self.env, metric_service)`
  to `__init__` (same construction shape `TrackingProcessor.__init__` already uses for its own
  `TrackingEngine`).
- `ChatService._apply_action_env` deleted entirely.
- `ChatService.apply_manual_action`'s call site: `self._apply_action_env(automaton, action, {})` →
  `self._tracking_engine.apply_action_env(automaton, action, {})`.

## Verification

- Full suite: 77 failed / 424 passed (post Prompt 10) → **77 failed / 424 passed**, identical
  failing-test set (`diff` of sorted `FAILED` lines empty) — zero regressions, nothing incidentally
  fixed either (expected: pure consolidation, no behavior change).
- Re-ran the manual-action-env-specific tests directly: `test_action_prompt_entry_point.py`,
  `test_regeneration_skips_signals.py`, `test_controller_action_on_enter.py` — 9/9 pass.
  (`test_chat_service_manual_action_env.py`, the file most directly about this exact feature, is
  currently failing for an unrelated, already-documented reason — Prompt 4's deliberate removal of
  `autotracking_on_user_message` from `Automaton`'s constructor, which that file's fixtures still
  pass; not touched here per the same "never fix the test" rule already applied throughout this
  session.)

No divergence was found between the two implementations, so there was nothing to fix "verso il
comportamento corretto secondo l'`avance_project_spec.md`" — both already matched it identically.
