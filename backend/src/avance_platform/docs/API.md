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

## Open question

Whether the Settings surface — backup/restore, Manage services, Manage
projects, warnings, runtime status, tasks — should be exported as a
*project service* rather than living in this package. It precedes this
work rather than following from it, so nothing here decided it, and
`settings_controller.py` should not be rearranged on the assumption that
it has been.
