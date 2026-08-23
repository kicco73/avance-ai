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

| Field         | Type              | Meaning                                                                 |
| ------------- | ----------------- | ------------------------------------------------------------------------ |
| `name`        | `string \| null`  | Session title, shown as its label in the Sessions panel.                 |
| `timestamp`   | `string \| null`  | Session start time, ISO 8601 UTC (e.g. `2026-08-20T12:34:56+00:00`).     |
| `datetime_end`| `string \| null`  | Session end time, ISO 8601 UTC.                                          |
| `start_state` | `string \| null`  | Automaton state the session started in.                                  |
| `end_state`   | `string \| null`  | Automaton state the session ended in (or currently sits in).             |
| `labeled`     | `boolean`         | Whether a reviewer has marked this session "done" (the Mark done button).|
| `comment`     | `string \| null`  | Reviewer's free-text note about the whole session.                       |
| `messages`    | `array`           | The session's messages, in chronological order. See below.               |

## Message object

Every message always carries these four fields:

| Field        | Type              | Meaning                                                                    |
| ------------ | ----------------- | --------------------------------------------------------------------------- |
| `role`       | `"user" \| "assistant"` | Who sent the message.                                                 |
| `text`       | `string`          | The message content.                                                        |
| `timestamp`  | `string \| null`  | When it was sent, ISO 8601.                                                 |
| `audio_text` | `string \| null`  | Raw speech-to-text transcription, when the message came in via voice and was edited before sending (`text` then holds the edited version). |

A message that triggered (or recorded) an automaton transition additionally
carries these seven fields. They are **omitted entirely** on a message with
no linked transition — don't assume they're present with `null` values:

| Field             | Type                                | Meaning                                                          |
| ----------------- | ------------------------------------ | ------------------------------------------------------------------ |
| `old_state`       | `string \| null`                     | Automaton state before this transition.                            |
| `action`          | `string \| null`                     | Name of the action/trigger that fired.                             |
| `new_state`       | `string \| null`                     | Automaton state after this transition.                             |
| `values`          | `object<string, number \| null> \| null` | Signal values recorded at this point — signal name to numeric value. |
| `expected_state`  | `string \| null`                     | Reviewer-annotated "should have been" state, used for benchmarking. |
| `expected_values` | `object<string, number \| null> \| null` | Reviewer-annotated expected signal values.                       |
| `comment`         | `string \| null`                     | Reviewer's note on this specific message/transition.                |

## Example

```json
[
  {
    "name": "Checkout walkthrough",
    "timestamp": "2026-08-20T12:34:56+00:00",
    "datetime_end": "2026-08-20T12:40:03+00:00",
    "start_state": "greeting",
    "end_state": "checkout_confirmed",
    "labeled": true,
    "comment": "Clean run, no issues.",
    "messages": [
      {
        "role": "user",
        "text": "Hi, I'd like to buy the blue jacket.",
        "timestamp": "2026-08-20T12:34:56+00:00",
        "audio_text": null
      },
      {
        "role": "assistant",
        "text": "Sure — what size?",
        "timestamp": "2026-08-20T12:34:58+00:00",
        "audio_text": null,
        "old_state": "greeting",
        "action": "start_purchase",
        "new_state": "asking_size",
        "values": { "cart_items": 1 },
        "expected_state": "asking_size",
        "expected_values": { "cart_items": 1 },
        "comment": null
      }
    ]
  }
]
```

## Notes and edge cases

- **Not every message has transition fields.** A message with no linked
  automaton transition only ever has `role`, `text`, `timestamp`,
  `audio_text`.
- **The opening transition is never exported.** A Tracking row with no
  linked message (the session's very first, implicit transition into
  `start_state`) is dropped on export; the importing side reconstructs it
  from `start_state` instead of expecting it in `messages`.
- **Round-tripping always produces an "imported" session**, even if the
  original was a live conversation. A live session is only meaningful
  against the exact database/automaton revision it actually ran against, so
  re-importing it elsewhere would misrepresent it as a real one.
- **Every field except `role`/`text` is optional.** A hand-written or
  externally generated file can omit any other key — `SessionImportJsonRequest`
  defaults everything else to `null`/`false`/`[]`.
- **Malformed input fails the whole session, not the whole file.** Each
  array entry is imported independently; one bad session doesn't abort the
  others (`import_session_json` rolls back just that session's own rows on
  error).
