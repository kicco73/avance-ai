# Debug report — `action_prompt`: dedicated entry point

## Problem

`ChatService._generate_action_prompt_message` used to call `self.process_turn(session_id, action.action_prompt)`,
treating `action.action_prompt` as if it were text the user had typed: it was saved to the DB as a
`role="user"` message and never cleaned up, permanently polluting the conversation history.

## 1. New `extra_prompt` parameter

Added `extra_prompt: str | None = None`, threaded through all three layers:

- `tracking/tracking_processor.py`: `TrackingProcessor.process(text, on_metadata=None, extra_prompt=None)`
  stores it as an instance attribute (`self.extra_prompt = extra_prompt`, same pattern as
  `self.metadata`/`self.out` — set fresh each turn, also defaulted to `None` in `__init__` for
  safety). `__build_turn_prompt_parts` appends it to `base_prompt` right after
  `state.contextual_prompt`, only when set:
  ```python
  base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
  if self.extra_prompt:
      base_prompt = f"{base_prompt}\n\n{self.extra_prompt}"
  ```
- `tracking/tracking_service.py`: `TrackingService.process(session_id, text, on_metadata=None, extra_prompt=None)`
  passes it straight through to `tracking_processor.process(text, on_metadata=..., extra_prompt=extra_prompt)`.
- `chat/chat_service.py`: `ChatService.process_turn(session_id, text=None, on_metadata=None, extra_prompt=None)`
  passes it straight through to `self.tracking_service.process(session_id, text, on_metadata, extra_prompt=extra_prompt)`.

## 2. `_generate_action_prompt_message`

```python
async def _generate_action_prompt_message(self, action: Action, session_id: int) -> dict:
	logger.warning("Executing action_prompt for action '%s'.", action.name)
	return await self.process_turn(session_id, None, extra_prompt=action.action_prompt)
```
`text=None` now (not `action.action_prompt`) — the `#FIXME` comment above this line is removed, the
problem it described (`action_prompt` polluting history) is what this prompt fixes.

`_generate_opening_message_body` is unchanged (`return await self.process_turn(session_id)`) —
`extra_prompt` stays at its default `None`, confirmed by a direct signature check
(`test_process_turn_extra_prompt_still_defaults_to_none`).

## 3. `_save_user_message` — the placeholder mechanism, and the `state.chat` check

Extracted the existing inline save/delete logic in `TrackingProcessor.process()` into
`_save_user_message(text)`:
```python
def _save_user_message(self, text: str | None) -> None:
	message_id = self.db.save_message("user", text or '...', self.user.session_id)
	self.user = replace(self.user, message_id=message_id, has_ai_started_conversation=not text)
```
(`UserVariables` gained two new fields, `message_id: int | None = None` and
`has_ai_started_conversation: bool = False`; `dataclasses.replace` was already imported but
unused — a hint this shape was anticipated.) `process()` then does, after the AI reply comes back:
```python
user_message_id = self.user.message_id
if self.user.has_ai_started_conversation and self.user.message_id:
	self.db.delete_message(self.user.message_id)
	user_message_id = None
```
— functionally identical to the old inline `if not text and user_message_id: ...`, since
`has_ai_started_conversation` is computed as exactly `not text`.

**The `state.chat` check, verified correct as-is:**
```python
if not state.chat and text not in (None, "", "..."):
	raise TrackingServiceError(...)
```
`text=None` is always in the exempt tuple, so this can never raise regardless of `state.chat`, for
any call with `text=None` — including `_generate_action_prompt_message`'s new call. Confirmed with
a dedicated test rather than by inspection alone: built a project where the action's *destination*
state (the one active by the time `_generate_action_prompt_message` actually runs — the transition
already lands before this call, see `ProjectService.apply_manual_action`) has `chat=False`, then
fired the action end-to-end. It succeeds and produces a reply
(`test_action_prompt_fires_even_when_the_destination_state_disallows_chat`). The `# FIXME` comment
on this line is removed — behavior confirmed correct without further changes.

## Tests written (`backend/tests/test_action_prompt_entry_point.py`, 4 tests, all `regression`)

- `test_action_prompt_leaves_no_user_role_message_in_the_db` — the constraint requested verbatim:
  after firing an action with `action_prompt`, every message saved for that turn has
  `role="assistant"`, none `role="user"`. (Asserts on *all* messages produced, not exactly one —
  a destination state that also needs its own opening message legitimately produces two assistant
  messages in the same turn, per `_messages_for_transition`/`PROJECT_SPECS.md` §6.3; the point is
  that none of them, ever, is `role="user"`.)
- `test_action_prompt_fires_even_when_the_destination_state_disallows_chat` — point 3 above.
- `test_action_prompt_text_reaches_the_model_prompt_not_the_saved_message` — confirms
  `action.action_prompt`'s literal text reaches the fake AI service's system prompt (via
  `extra_prompt`) while never appearing in any persisted message's content.
- `test_process_turn_extra_prompt_still_defaults_to_none` — signature-level guard for the
  "`_generate_opening_message_body` doesn't change" constraint.

## Verification

- New test file: 4/4 pass.
- Full suite: 76 failed / 420 passed (post Prompt 4) → **76 failed / 424 passed** (+4, the new
  tests; failing-test set is byte-for-byte identical — confirmed via `grep ^FAILED | sort` diff, no
  new failures, nothing fixed incidentally).
