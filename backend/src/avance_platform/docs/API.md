# Route decisions

Each of these looked like an obvious improvement and is not. If you are
about to make one, this is the argument you are arguing against. The rule
they sit under is `docs/API.md`.

## The six annotation routes were not merged into two

`PUT /api/skills/platform/sessions/{id}/labeled|title|comment` and
`PUT /api/skills/platform/messages/{id}/expected-state|expected-signals|comment`
look like one partial update of a session and one of a message. They are
not merged, and should not be.

Every caller writes exactly one field, each from its own affordance — the
"Mark done" button, the Info tab's title, the Info tab's note, the States
tab, the Signals tab — and each already guards against a no-op write. No
caller ever sends two. So a merged route would need a partial-update
protocol that nothing exercises: `null` *clears* title and comment, so
"field absent" and "field present and null" have to be told apart, which
means inspecting which keys the payload happened to carry.

That is one endpoint serving several contexts according to which field is
set, which is the flag design wearing REST clothes. It would also fuse
three different error contracts into one: `expected-state` answers 409
for a message that is not an evaluation point and 422 for an unknown
state, while `comment` deliberately answers neither, because every
message is a legitimate target for a note.

Six small routes, each with one caller, one payload and one error
contract, are the honest model of what the Label sessions view does.

## `POST .../projects/upload` kept its name

`upload` is a verb, and `POST .../projects` already creates. But the
route is one half of the Download/Upload pair the Settings view presents,
and the name is the user's word for it. Renaming it to `imports` would
have made the path read better and the screen read worse.

## Project CRUD stayed in `settings_controller.py`

Eight `/api/skills/platform/projects/…` routes in a file otherwise
serving `/api/skills/platform/settings/…` looks like the largest
violation in the codebase. It is a deliberate split, and the file says
so: this half is a project's lifecycle *as a whole object* (list, create,
switch, download/upload, delete), while `edit_project_controller.py` has
any one field inside it. Manage projects is a Settings screen. Only the
prefix changed.

## `PUT .../users/{id}/role` stayed a PUT

It sets a field, which is what PUT is for. Not everything that was not
converted to POST was overlooked.

## `POST .../index-yml/modernize` is the editor's, not the builder's

`index.yml` can be written in a spelling the format has moved past but
still reads exactly — `project.talk-enabled` for `services: {talk: …}`.
Something has to rewrite those, and every place that already had the file
in its hands was the wrong one.

The builder was the obvious candidate and is the worst: it is the one
thing in the product that never edits what it is given, and a project is
built on every load, every health check, every session — turning that
into a write would mean a read path that saves, at a revision an author
may not even be looking at. The loader, the uploader and the save path
each had the same problem in a smaller way: whichever one ran first
would have silently changed a file, and told nobody.

So the rewrite is a route of the editor, and "Edit project" posts to it
on open. The author is present by construction; they get a dialog saying
what changed, and the banner they were about to read has one fewer
warning in it. What the fixer does not know how to rewrite it leaves
alone, still warned about — the two halves are read from the same list
(`automaton/deprecations.py`), so neither can drift from the other.

This is also why the route is the skill's and not the core's: a build
without this package has no editor, nobody to show a dialog to, and no
business rewriting anyone's project.

## Open question

Whether the Settings surface — backup/restore, Manage services, Manage
projects, warnings, runtime status, tasks — should be exported as a
*project service* rather than living in this package. It precedes this
work rather than following from it, so nothing here decided it, and
`settings_controller.py` should not be rearranged on the assumption that
it has been.
