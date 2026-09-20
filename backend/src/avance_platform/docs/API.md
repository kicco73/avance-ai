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

## `GET .../settings/projects/apps` exists so Manage projects can never go blank

The store and the manage view ask the same question — what does the
*published* revision of each project offer (icon, family, reactions,
compiled) — and need opposite answers when that revision no longer
builds. `app-store/apps` drops it: nobody should be able to install an
app that cannot run. `settings/projects/apps` keeps it, built from an
`UnbuildableRevision` in place of the automaton, because the admin
looking at that project is the one person who can fix it.

Manage projects read the store's list once. A project stopped because
its published revision was broken then vanished from the centre panel,
which said "hasn't been published yet", so the Publish button that would
have shipped the repaired draft was not on screen — the one screen that
can end the outage was the one screen that hid it.

**This view must never again lose its panel for a broken project.**
`manageProjectsBrokenPublishedRevision.test.js` selects such a project
and asserts the panel, its revision and its Publish button; it fails the
moment the view is pointed back at the store's catalogue. Do not rewire
this data source — not while changing a transition, a layout, or a
catalogue call that looks redundant.

## `PUT .../users/{id}/role` stayed a PUT

It sets a field, which is what PUT is for. Not everything that was not
converted to POST was overlooked.

## There is no `POST .../index-yml/modernize`

There was one, and "Edit project" awaited it on open before loading
anything else. It was redundant: the builder refuses every spelling it
does not read, so a legacy `index.yml` fails to build, and a revision
that fails to build is already rewritten in place by the loader
(`project/archive/stored_index_yml.py`) on the first load of the mount.
The route could never find anything the loader had not settled, and
its only visible effect was a dialog listing the fixes — paid for with a
POST on the critical path of a read-only view, plus the full
`projectChanged` refresh cascade that every `projectFetch` triggers.
Where an `index.yml` is rewritten is `PROJECT_SPECS.md` §8.2.

## Open question

Whether the Settings surface — backup/restore, Manage services, Manage
projects, warnings, runtime status, tasks — should be exported as a
*project service* rather than living in this package. It precedes this
work rather than following from it, so nothing here decided it, and
`settings_controller.py` should not be rearranged on the assumption that
it has been.

## `POST .../websearch-sources` is its own route, not a parameter

`POST .../sources` already creates a source, so a `driver=websearch`
query parameter looks like the smaller change. It is the flag design
again: the two creations share a name and nothing else. The `avance` one
takes a body and an optional `file_name` (the CSV upload path), writes a
`sources/<id>.csv` archive before the project revalidates, and points
`url` at it; the websearch one takes no payload at all, writes no
archive, and sets `url` to the single fixed `websearch:user`. One route
per creation keeps each with one payload, one side effect and one
caller — the explorer's "Add source" and "Add web search" menu items.
