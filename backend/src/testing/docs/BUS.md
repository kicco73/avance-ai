# What Testing says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it. It publishes and subscribes to no message type: a replay drives a
session through the same single door every conversation uses
(`session.enter` / `session.create`), and reads what comes back.

| Point | What it answers |
| --- | --- |
| `core.services` | its own service, so whatever is composed after it can reach measurement without importing it |
| `config.services` | its own section of the public services snapshot |
| `http.controllers` | its routes, under `/api/skills/testing/` |

Two things it reads that nothing else does, and that are worth knowing
when either changes:

- **It reads every session type**, not only `live`. A session type that
  learns to destroy itself must exempt `test`, or a run loses the
  sessions it was measuring.
- **It runs the automaton for real and suppresses every `task.*` call.**
  See this package's own `PROJECT_SPECS.md` section for what that means
  for a project author.
