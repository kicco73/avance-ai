# What Listen says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

**Subscribes** to `input.audio` — the bytes, or a callable that fetches
them. `decoder.py` transcribes and publishes `input.text` **on the same
envelope**, so what the core runs is an ordinary text turn and nothing
downstream knows the person spoke. The publisher of the audio never takes
the transcript and re-publishes it itself; the conversion happens here or
not at all.

The callable form is why: a message no decoder in this build will read is
never downloaded. A publisher that wants to know whether anything
happened uses `publish_with_bounceback` — the audio coming back is the
answer that nothing in this build transcribes.

**Contributes**:

| Point | What it answers |
| --- | --- |
| `api.state` | that this installation can transcribe, for the frontend's boot state |
| `config.services` | its own section of the public services snapshot |
| `http.controllers` | its routes, under `/api/skills/listen/` |
| `session.services` | what transcription can do for **one** conversation. The session's own project may narrow the server's switch, never widen it |

The speech model loads in the background after start, so the capability
reports itself unavailable for the first moments and available
afterwards.
