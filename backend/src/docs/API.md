# The HTTP surface

## The rule

**The first segment after `/api/` names whoever answers.**

```text
GET  /api/skills              the collection — what this backend has installed
     /api/skills/platform/…   avance_platform (key "platform")
     /api/skills/webchat/…    webchat
     /api/skills/testing/…    testing
     /api/skills/build/…      build
     /api/skills/talk/…       talk
     /api/skills/listen/…     listen
     /api/skills/whatsapp/…   whatsapp
     /api/core/…              no skill owns it: every build answers it
WS   /api/core/bus            the bus, seen from the browser
```

This is the same statement `system/skills.py` already makes on the filesystem, moved
onto the URL. A skill is a directory and that directory is the switch (see
`BUILDING_PLATFORMS.md`); so `/api/skills/talk/*` answers if and only if
`backend/src/talk/` was copied. A 404 there is not a bug to investigate — it is a
build without that package, and you can confirm it by asking `GET /api/skills`.

`core` is named rather than left bare. A missing prefix would still be a convention
to remember, and the only invisible one; naming it means the rule has no special
cases and the check is one line.

`GET /api/skills` is the single route outside every prefix, and has to be: it is the
list of which prefixes exist at all, so a build that left out the package answering
it could not be asked what it contains.

### It is checked, not remembered

`backend/tests/test_route_ownership.py` reads the decorators statically and fails on
any route declared outside its own package's prefix. It held by convention before,
and convention is exactly what it could not survive: `/api/chat/` was taken to mean
webchat's, and over time the editor's inspector, the labelling screen and talk all
registered under it. Two of the chat window's own routes ended up in the labelling
controller, which a build without an editor does not have — and nothing failed,
because in a full build every package is present.

`frontend/tests/skillBoundaries.test.js` is the twin on the other side.

## What HTTP answers, and what it does not

**One conversation is on the bus. Many sessions are administered over HTTP.**

Everything about the conversation you are in — which session it is, what was said
in it, where its automaton stands, what it offers, whether it is blocked, whether
it has ended — is a message on `/api/core/bus`, and `BUS.md` is the vocabulary.
Webchat has no HTTP surface left at all: `webchat_controller.py` is gone, and with
it `sessions/current`, `POST sessions`, `messages` and `operator-state`. So are
`state`, `close`, `audio` and `messages/{id}/reaction` from
`turn/session_controller.py`.

What stayed is the administration of sessions nobody is having:

```text
GET    /api/core/sessions/{id}/history      someone else's transcript, read by the
                                            editor, the labelling screen, benchmark
GET    /api/core/sessions/{id}/signals      the inspector
GET|PUT /api/core/sessions/{id}/actuators   the inspector
POST   /api/core/sessions/{id}/truncate     the Run panel's "restart from here",
                                            which keeps a prefix that closing and
                                            recreating would lose
DELETE /api/core/sessions/{id}
```

plus the per-project listings in `avance_platform`.

Which side something belongs on is decided by the envelope, not by taste. A message
about one conversation has a `session_id`, or a `project_id` when the session does
not exist yet, and the bus delivers it to whoever is watching that session. A
listing across many sessions has neither, and a route is the honest shape for it.

## Decisions taken

- **Two endpoints were deleted rather than renamed.** `GET /api/build/skills` and
  `GET /api/services` were both projections of `skills.installed()` under different
  names, one of them inside a package a build can leave out. `GET /api/skills`
  replaced both; callers wanting the declarable subset filter the `declarable` field
  each row already carries.
- **`GET .../build/skills` became `.../build/requirements`.** It is no longer the
  roster — that is `GET /api/skills` — but what one project makes of it: `required`,
  `disabled`, `contradicted`.
- **The websocket moved under `/api/` and was renamed.** `system/ws_notifications.py`
  carried no notifications: it forwards four bus message types to the browser and
  republishes onto the bus what a client sends. `ui.notification` was one of the four
  payloads, not the channel. It is `system/bus_channel.py` and `/api/core/bus`.
- **Verbs became resources** where the path described an action rather than a thing:
  `actions`, `invitations/{code}`, `auth/terms/acceptance`, `legal-terms/status`,
  `legal-terms/acceptance`; `POST` on the lifecycle commands (pause, resume,
  activate), `PUT` on the toggles (autotracking, actuators).

## Decisions *not* taken, and why

Recorded because each looked like an obvious improvement and is not. If you are about
to do one of these, this is the argument you are arguing against.

### The six annotation routes were not merged into two

`PUT /api/skills/platform/sessions/{id}/labeled|title|comment` and
`PUT /api/skills/platform/messages/{id}/expected-state|expected-signals|comment` look
like one partial update of a session and one of a message. They are not merged, and
should not be.

Every caller writes exactly one field, each from its own affordance — the "Mark done"
button, the Info tab's title, the Info tab's note, the States tab, the Signals tab —
and each already guards against a no-op write. No caller ever sends two. So a merged
route would need a partial-update protocol that nothing exercises: `null` *clears*
title and comment, so "field absent" and "field present and null" have to be told
apart, which means inspecting which keys the payload happened to carry.

That is one endpoint serving several contexts according to which field is set, which
is the flag design wearing REST clothes. It would also fuse three different error
contracts into one: `expected-state` answers 409 for a message that is not an
evaluation point and 422 for an unknown state, while `comment` deliberately answers
neither, because every message is a legitimate target for a note.

Six small routes, each with one caller, one payload and one error contract, are the
honest model of what the Label sessions view does.

### `POST /api/skills/platform/projects/upload` kept its name

`upload` is a verb, and `POST .../projects` already creates. But the route is one half
of the Download/Upload pair the Settings view presents, and the name is the user's
word for it. Renaming it to `imports` would have made the path read better and the
screen read worse.

### `build/local-module` and `build/backend-copy` kept theirs

Same reason: they are the Build view's own step labels, "Local module" and "backend
copy". The correspondence between what a person clicks and what the request is called
is worth more here than the noun.

### Project CRUD stayed in `settings_controller.py`

Eight `/api/skills/platform/projects/…` routes in a file otherwise serving
`/api/skills/platform/settings/…` looks like the largest violation in the codebase.
It is a deliberate split, and the file says so: this half is a project's lifecycle
*as a whole object* (list, create, switch, download/upload, delete), while
`edit_project_controller.py` has any one field inside it. Manage projects is a
Settings screen. Only the prefix changed.

### `PUT /api/skills/platform/users/{id}/role` stayed a PUT

It sets a field, which is what PUT is for. Not everything that was not converted to
POST was overlooked.

## Open question

Whether the Settings surface — backup/restore, Manage services, Manage projects,
warnings, runtime status, tasks — should be exported as a *project service* rather
than living inside `avance_platform`. It precedes this work rather than following
from it, so nothing here decided it, and `settings_controller.py` should not be
rearranged on the assumption that it has been.

## A note on sweeping renames

The migration rewrote about a thousand literals across both halves of the project.
Three blind spots are worth knowing about, because each produced a different kind of
silence:

1. **A path segment is not a word.** Matching one segment as "no slash, quote or
   backtick" silently excludes an f-string interpolating a quoted key —
   `{session['id']}/action`. Those failed loudly, in tests that assert on data.
2. **The same string can be a URL or an import.** `/api/chat/` → `/api/skills/webchat/`
   also rewrote `../../api/chat.js` into a module that does not exist. The suite stayed
   green while the bundle would not build; only `vite build` saw it.
3. **The two halves spell the same path differently.** The backend writes
   `"/api/auth/…"`; the frontend writes `` `${API_URL}/auth/…` `` with `API_URL`
   already ending in `/api`. A sweep matching the first form leaves the second
   untouched, and thirty-eight frontend calls sat on `master` returning 404 — invisible
   to tests that mock the fetch layer, and to a rule that only checks the first segment,
   since `/auth` is a plausible first segment. What catches this class is a check that
   every path the frontend requests is a path the backend declares.

— Claude Opus 5, 2026-09-11
