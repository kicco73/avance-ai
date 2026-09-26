# Session Export Format

The JSON format produced by the "Label sessions" view's **Download all**
button, and consumed back by its **Import** button. One file round-trips
every session of a project.

## Top level

The exported file is a **JSON array of session objects** — not a single
object. Every session of the project is included, whether it was a real
("live") conversation or a previously imported one.

```json
[
  { "...": "one session object, see below" },
  { "...": "another session object" }
]
```

## Session object

| Field          | Type             | Meaning                                                                                                                                                                                   |
|----------------|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`         | `string \| null` | Session title, shown as its label in the Sessions panel.                                                                                                                                  |
| `username`     | `string \| null` | The session's owner. Preserved on reimport when present; a `.txt`-style import with no `username` generates a fresh test user instead.                                                    |
| `type`         | `string \| null` | The session's original kind — `"live"` or `"imported"`. Preserved on reimport; missing or any other value falls back to `"imported"` (e.g. an export produced before this field existed). |
| `timestamp`    | `string \| null` | Session start time, ISO 8601 UTC (e.g. `2026-08-20T12:34:56+00:00`).                                                                                                                      |
| `datetime_end` | `string \| null` | Session end time, ISO 8601 UTC.                                                                                                                                                           |
| `start_state`  | `string \| null` | Automaton state the session started in.                                                                                                                                                   |
| `end_state`    | `string \| null` | Automaton state the session ended in (or currently sits in).                                                                                                                              |
| `labeled`      | `boolean`        | Whether a reviewer has marked this session "done" (the Mark done button).                                                                                                                 |
| `comment`      | `string \| null` | Reviewer's free-text note about the whole session.                                                                                                                                        |
| `closed_at`    | `string \| null` | When the session was explicitly closed, ISO 8601 UTC — `null` for a session still open, or one that only ever expired by its open window.                                                 |
| `close_reason` | `string \| null` | Why the session was explicitly closed — one of `"channel-switch"`, `"force-new-session"`, `"manual-user"`, `"manual-assistant"`, `"revision-invalid"`, `"final-state"`. Always `null` when `closed_at` is `null`.                |
| `messages`     | `array`          | The session's messages, in chronological order. See below.                                                                                                                                |

## Message object

Every message always carries these four fields:

| Field        | Type              | Meaning                                                                    |
| ------------ | ----------------- | --------------------------------------------------------------------------- |
| `role`       | `"user" \| "assistant"` | Who sent the message.                                                 |
| `text`       | `string`          | The message content.                                                        |
| `timestamp`  | `string \| null`  | When it was sent, ISO 8601.                                                 |
| `audio_text` | `string \| null`  | Raw speech-to-text transcription, when the message came in via voice and was edited before sending (`text` then holds the edited version). |

A message optionally also carries `tokens` — its token cost (input tokens
for a `user` message, output tokens for an `assistant` one), when the AI
provider reported one for it. **Omitted entirely**, never `null`, when
unknown (e.g. an export produced before this field existed, or a message a
provider never reported usage for).

| Field    | Type      | Meaning                     |
| -------- | --------- | --------------------------- |
| `tokens` | `integer` | Token cost of this message. |

A message that triggered (or recorded) an automaton transition additionally
carries these eight fields. They are **omitted entirely** on a message with
no linked transition — don't assume they're present with `null` values:

| Field             | Type                                     | Meaning                                                                                                                                     |
|-------------------|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| `old_state`       | `string \| null`                         | Automaton state before this transition.                                                                                                     |
| `action`          | `string \| null`                         | Name of the action/trigger that fired.                                                                                                      |
| `new_state`       | `string \| null`                         | Automaton state after this transition.                                                                                                      |
| `values`          | `object<string, number \| null> \| null` | Signal values recorded at this point — signal name to numeric value.                                                                        |
| `expected_state`  | `string \| null`                         | Reviewer-annotated "should have been" state.                                                                                               |
| `expected_values` | `object<string, number \| null> \| null` | Reviewer-annotated expected signal values.                                                                                                  |
| `comment`         | `string \| null`                         | Reviewer's note on this specific message/transition.                                                                                        |
| `origin`          | `string \| null`                         | Why this transition was written — one of `"trigger"`, `"manual"`, `"system"`, `"init-action"`, or `null` for a row with no recorded origin. |

## Action entry

A button pressed or a choice picked — a manual action, which is not a
message — is an entry of its own in `messages`, with `role: "action"`, at
the place where it happened: after every message that came before it, before
every one after it. The assistant message the action produced, if any (an
`ai` state that answers right away, the `chat.write` of a `system` state),
follows it as a normal `assistant` entry.

| Field            | Type                                     | Meaning                                                                                  |
|------------------|------------------------------------------|------------------------------------------------------------------------------------------|
| `role`           | `"action"`                               | Marks the entry as a manual action.                                                       |
| `action`         | `string`                                 | The pressed action's name. Exactly one of `action` and `choice`.                          |
| `choice`         | `{"key": string, "option": string}`      | The picked choice: its `list` env key and the option. Exactly one of `action` and `choice`. |
| `timestamp`      | `string \| null`                         | When it was pressed, ISO 8601.                                                            |
| `old_state`      | `string \| null`                         | State it was pressed in.                                                                  |
| `new_state`      | `string \| null`                         | State it led to.                                                                          |
| `values`         | `object<string, number \| null> \| null` | The last signal values measured when it was pressed.                                     |
| `origin`         | `"manual"`                               | Always `"manual"`.                                                                        |
| `expected_state` | `string \| null`                         | Reviewer-annotated "should have been" state after it.                                    |
| `comment`        | `string \| null`                         | Reviewer's note on it.                                                                    |

Export writes every field. A hand-written file needs only `role` and one of
`action`/`choice`; an entry with neither or both fails its session. It has
no `text` and no `expected_values`: an action asks the model for nothing.
Its position is kept exactly — by how many messages came before it, not by
its timestamp — so export → import → export gives the same file back.

```json
{ "role": "action", "action": "start", "timestamp": "2026-09-26T09:00:10+00:00",
  "old_state": "welcome", "new_state": "opening", "values": null, "origin": "manual",
  "expected_state": "opening", "comment": null }
```

```json
{ "role": "action", "choice": { "key": "level", "option": "hard" },
  "timestamp": "2026-09-26T09:00:12+00:00", "expected_state": "opening", "comment": null }
```

## Example

```json
[
  {
    "name": "Checkout walkthrough",
    "username": "alice@example.com",
    "type": "live",
    "timestamp": "2026-08-20T12:34:56+00:00",
    "datetime_end": "2026-08-20T12:40:03+00:00",
    "start_state": "greeting",
    "end_state": "checkout_confirmed",
    "labeled": true,
    "comment": "Clean run, no issues.",
    "closed_at": "2026-08-20T12:40:03+00:00",
    "close_reason": "manual-user",
    "messages": [
      {
        "role": "action",
        "action": "start",
        "timestamp": "2026-08-20T12:34:50+00:00",
        "old_state": "welcome",
        "new_state": "greeting",
        "values": null,
        "origin": "manual",
        "expected_state": "greeting",
        "comment": null
      },
      {
        "role": "user",
        "text": "Hi, I'd like to buy the blue jacket.",
        "timestamp": "2026-08-20T12:34:56+00:00",
        "audio_text": null,
        "tokens": 42
      },
      {
        "role": "assistant",
        "text": "Sure — what size?",
        "timestamp": "2026-08-20T12:34:58+00:00",
        "audio_text": null,
        "tokens": 18,
        "old_state": "greeting",
        "action": "start_purchase",
        "new_state": "asking_size",
        "values": { "cart_items": 1 },
        "expected_state": "asking_size",
        "expected_values": { "cart_items": 1 },
        "comment": null,
        "origin": "trigger"
      }
    ]
  }
]
```

## Notes and edge cases

- **Not every message has transition fields.** A message with no linked
  automaton transition only ever has `role`, `text`, `timestamp`,
  `audio_text`, and optionally `tokens`.
- **The opening transition is never exported.** A Tracking row with no
  linked message (the session's very first, implicit transition into
  `start_state`) is dropped on export — a manual action is the one row with
  no message that is exported, as an action entry; the importing side reconstructs it
  from `start_state` instead of expecting it in `messages`.
- **Round-tripping preserves the original `type`.** A live session reimports
  as `"live"` again, not force-converted to `"imported"` — `username` is
  preserved the same way. Only a file with no `type` at all (an export
  produced before this field existed) falls back to `"imported"`.
- **Every field except `role`/`text` is optional** (`role` and
  `action`/`choice` for an action entry). A hand-written or
  externally generated file can omit any other key — `SessionImportJsonRequest`
  defaults everything else to `null`/`false`/`[]`.
- **Malformed input fails the whole session, not the whole file.** Each
  array entry is imported independently; one bad session doesn't abort the
  others (`import_session_json` rolls back just that session's own rows on
  error).
