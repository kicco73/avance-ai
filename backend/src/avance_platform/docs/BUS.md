# What the platform says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

| Point | What it answers |
| --- | --- |
| `project.published` | it *asks* this one: a publish answers with whatever anyone able to turn a revision into a package added to the report, and an installation where nobody can collects nothing |
| `config.services` | its own section of the public services snapshot |
| `http.controllers` | its routes, under `/api/skills/platform/` |

It claims no loader at `automaton.loader`: the Db/Archive-backed one the
core builds by default *is* the authoring surface's, so there is nothing
to replace.

It reads sessions over HTTP rather than over the Bus: a listing across
many sessions has no `session_id` and no
`project_id` of one conversation, so a route is the honest shape for it
(`docs/API.md`).

Nothing here enters or creates a session. The editor's Test chat and the
app store's preview open the way every other conversation does, by saying
`session.enter` or `session.create` on the socket — their own
`test-sessions` and `preview-sessions` route pairs are gone.
