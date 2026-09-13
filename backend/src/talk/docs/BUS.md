# What Talk says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

**Subscribes** to `output.speech` — the spoken version of a reply,
written by the model alongside the reply itself. It starts synthesizing
there and then and publishes `output.audio_stream` on the same envelope.
A later `output.speech` for the same exchange replaces the earlier one.

**Contributes**:

| Point | What it answers |
| --- | --- |
| `api.state` | that this installation can speak, for the frontend's boot state |
| `config.services` | its own section of the public services snapshot |
| `http.controllers` | its routes, under `/api/skills/talk/` |
| `turn.spoken_reply` | `ask()` — it is the one that can speak. Whoever runs the interface supplies `want()` |
| `session.services` | what speaking can do for **one** conversation. The session's own project may narrow the server's switch, never widen it |

Nothing here asks whether anybody wants a spoken reply before publishing:
the publisher learns from the posting itself. A build where nobody is
registered for `output.audio_stream` is a normal outcome, not an error.
