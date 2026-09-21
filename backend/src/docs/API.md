# The HTTP surface

## The rule

**The first segment after `/api/` names whoever answers.**

```text
GET  /api/skills              the collection — what this backend has installed
     /api/skills/<key>/…      the skill that declared that key
     /api/core/…              no skill owns it: every build answers it
WS   /api/core/bus            the bus, seen from the browser
```

This is the same statement `system/skills.py` already makes on the
filesystem, moved onto the URL. A skill is a directory and that directory
is the switch, so `/api/skills/<key>/*` answers if and only if that
package was copied. A 404 there is not a bug to investigate — it is a
build without that package, and you can confirm it by asking
`GET /api/skills`.

`core` is named rather than left bare. A missing prefix would still be a
convention to remember, and the only invisible one; naming it means the
rule has no special cases and the check is one line.

`GET /api/skills` is the single route outside every prefix, and has to
be: it is the list of which prefixes exist at all, so a build that left
out the package answering it could not be asked what it contains.

### It is checked, not remembered

`backend/tests/test_route_ownership.py` reads the decorators statically
and fails on any route declared outside its own package's prefix. It held
by convention before, and convention is exactly what it could not
survive: one shared prefix was taken to mean one package's, and over time
three unrelated screens registered under it. Two of a chat window's own
routes ended up in a controller that a build without an editor does not
have — and nothing failed, because in a full build every package is
present.

`frontend/tests/skillBoundaries.test.js` is the twin on the other side.

## What HTTP answers, and what it does not

**One conversation is on the bus. Many sessions are administered over
HTTP.**

Everything about the conversation you are in — which session it is, what
was said in it, where its automaton stands, what it offers, whether it is
blocked, whether it has ended — is a message on `/api/core/bus`, and
`docs/BUS.md` is the vocabulary. No route resolves or creates a session,
for any kind of it: a conversation opens by saying `session.enter` or
`session.create`.

What stayed is the administration of sessions nobody is having:

```text
GET    /api/core/sessions/{id}/history      someone else's transcript
GET    /api/core/sessions/{id}/signals      the inspector
GET|PUT /api/core/sessions/{id}/actuators   the inspector
POST   /api/core/sessions/{id}/truncate     "restart from here", which keeps a
                                            prefix that closing and recreating
                                            would lose
GET|POST /api/core/sessions/{id}/rating     the user's thumb up/down for the
                                            session's own (project, revision),
                                            keyed by that pair so a later
                                            revision asks again
DELETE /api/core/sessions/{id}
```

Which side something belongs on is decided by the envelope, not by taste.
A message about one conversation has a `session_id`, or a `project_id`
when the session does not exist yet, and the bus delivers it to whoever
is watching that session. A listing across many sessions has neither, and
a route is the honest shape for it.

## How a failure becomes a status

An endpoint does not choose its own status code. It raises, and one
handler registered in `main.py` — `ApiErrorHandlers.register`, see
`error_handlers.py` — turns the exception into the
`{error: {message, detail}}` body every failure shares:

```text
PermissionError     403
FileNotFoundError   404
ValueError          400
AIServiceError      its own status_code
ServiceError        its own status_code, plus code and fields
anything else       500, logged
```

Starlette resolves a handler by walking the exception's MRO, so a
subclass is covered by its base's handler with no registration of its
own. That is what makes the list short: `TurnServiceError`,
`TrackingServiceError` and every future sibling arrive through
`ServiceError`, and `AutomatonBuildError` — which is both a `ServiceError`
and a `ValueError` — is resolved by the `ServiceError` handler because
`ServiceError` comes first in its MRO. A service that needs a status the
table does not give it raises a `ServiceError` subclass saying so; that
is the only mechanism, and there is no second one.

This used to be written out per endpoint instead. Sixty-seven
`try/except` blocks across thirteen controllers repeated the same three
clauses, and thirty-three of them existed only to re-raise
`AutomatonBuildError` so that the `except ValueError` on the next line
would not swallow it into a 400 that lost its fields. The controllers now
let the exception travel.

The consequence worth knowing: the translation is no longer opt-in. A
`FileNotFoundError` or a `ValueError` escaping *any* route is answered
404 or 400, including routes that used to let one through to a 500.

## Decisions taken

- **Two endpoints were deleted rather than renamed.** Both were
  projections of `skills.installed()` under different names, one of them
  inside a package a build can leave out. `GET /api/skills` replaced
  both; callers wanting the declarable subset filter the `declarable`
  field each row already carries.
- **The websocket moved under `/api/` and was renamed.**
  `system/ws_notifications.py` carried no notifications: it forwards four
  bus message types to the browser and republishes onto the bus what a
  client sends. `ui.notification` was one of the four payloads, not the
  channel. It is `system/bus_channel.py` and `/api/core/bus`.
- **Verbs became resources** where the path described an action rather
  than a thing: `invitations/{code}`, `auth/terms/acceptance`,
  `legal-terms/status`, `legal-terms/acceptance`; `POST` on the lifecycle
  commands (pause, resume, activate), `PUT` on the toggles (autotracking,
  actuators).

A skill that made a decision of its own about its own routes records it
in its own documentation, not here.

## A note on sweeping renames

The migration rewrote about a thousand literals across both halves of the
project. Three blind spots are worth knowing about, because each produced
a different kind of silence:

1. **A path segment is not a word.** Matching one segment as "no slash,
   quote or backtick" silently excludes an f-string interpolating a
   quoted key — `{session['id']}/action`. Those failed loudly, in tests
   that assert on data.
2. **The same string can be a URL or an import.** Rewriting a route
   prefix also rewrote `../../api/<name>.js` into a module that does not
   exist. The suite stayed green while the bundle would not build; only
   `vite build` saw it.
3. **The two halves spell the same path differently.** The backend writes
   `"/api/auth/…"`; the frontend writes `` `${API_URL}/auth/…` `` with
   `API_URL` already ending in `/api`. A sweep matching the first form
   leaves the second untouched, and thirty-eight frontend calls sat on
   `master` returning 404 — invisible to tests that mock the fetch layer,
   and to a rule that only checks the first segment, since `/auth` is a
   plausible first segment. What catches this class is a check that every
   path the frontend requests is a path the backend declares.
