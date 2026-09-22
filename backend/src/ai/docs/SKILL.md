## AI

A language model to answer with: every state whose reply is written by
the model, rather than scripted by hand, and the small stand-alone calls
a `task:` script makes on its own (`task.prompt`, `task.websearch`).
Needs at least one provider configured, with credentials.

Without it, a project answers only through states declared to answer a
different way — with a human operator, or with what an action's own
script writes directly — and `task.prompt`/`task.websearch` return an
empty result instead of a generation. A project that declares any state
`input-processor: ai` needs this skill to run at all; a build works that
out for itself.
