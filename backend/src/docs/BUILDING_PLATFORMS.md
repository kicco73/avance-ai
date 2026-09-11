# Building a platform out of skills

This is the architecture: how a platform is cut into skills, isolated and
assembled into a delivery. What each skill actually does for the people
using it is `docs/SKILLS.md`.

The product is sold by skill, and the customer receives the code. So a
build does not merely *disable* what was not bought: it must not ship it
at all. One rule carries the whole design, on both sides of the wire:

> **A skill is a directory, and that directory is the switch.**

Nothing else records the choice — no manifest, no flag, no feature
table, nothing to read back at run time. What was not copied is not
there, and cannot be found, listed, re-enabled, or read.

## Backend — done

```
backend/src/<package>/
  skill.py            the declaration: one Skill subclass, nothing lists it
  <name>_controller.py  its routes, all under /api/skills/<key>/
  config.py           its own section of .config.yml
  tests/              its tests, and nobody else's
```

`system/skills.py::discover()` walks `backend/src/` with pkgutil at boot
and starts whatever is there. `build/backend_copy.py::copy_backend`
copies the tree with an `ignore` that drops, at the `backend/src/` level
only, the directories named in `excluded_skills`; `_write_requirements`
then prunes the pip lines those skills declared in `requirements()`, so a
build without Talk stops asking pip for `piper-tts`.

Two consequences worth naming, because they are the point rather than
side effects:

- `GET /api/skills/<key>/…` exists if and only if `<key>` is installed. A
  404 there is a build without that package, not a bug to investigate.
- A skill's tests leave with it. See `docs/TESTS.md` for why a directory
  beats a marker: pytest imports a test module before `-m` can deselect
  it, so a marker leaves a collection error behind where a missing
  directory leaves nothing. `conftest.installed_skill(package)` is how a
  core test skips instead of failing when a package is absent.

### How a skill reaches something it must not name

Nothing in the design forbids skills from needing each other's work; what
is forbidden is knowing who does it. `system/bus.py` is that seam, and
`docs/BUS.md` is its reference. A skill needing audio transcribed
publishes `input.audio` rather than importing `listen`, and **a build
where nobody is registered for that type is a normal outcome, not an
error** — which is precisely what a build with that skill removed looks
like. `publish_with_bounceback` makes undeliverable itself a delivery, so
the producer says what it means once, in a named method, instead of
branching at every call site on whether a package it must not name is
installed.

Contribution points do the same for assembled answers: a skill hands its
tab to Settings (`POINT_CONFIG_SERVICES`), its controllers to the router
(`POINT_HTTP_CONTROLLERS`), its capability flag to the frontend's boot
state (`POINT_API_STATE`), and — where it can compile — the package it
made out of a revision somebody just published (`POINT_PROJECT_PUBLISHED`).
That last one is worth reading twice: publishing asks nothing about
whether a compiler is installed, it collects, and a build without one
collects nothing. The core assembles what arrived and names nobody. `ui_label`/`ui_description` travel the same way, so even the
*words* on screen for a skill come from the skill.

Two skills can even negotiate without meeting: `product` stands down from
`POINT_AUTOMATON_LOADER` when `build` is installed, because a backend
that can compile should decide per project and revision. Neither imports
the other; the point they both answer composes them.

### A skill describes itself, too

The same rule reaches the documentation. `docs/SKILLS.md` in the core is
only an introduction; each skill writes its own section in
`backend/src/<package>/docs/SKILL.md`, and `skills.documentation()`
assembles the sections of whatever is installed. A skill that was not
copied contributes nothing, so the page a customer reads describes the
product they were given and never one somebody else bought — for the same
reason, and by the same mechanism, that its routes are not there either.

`avance_platform/doc_catalog.py` keeps this out of the controller: a doc
slug maps to an object that knows how to render itself, a file on disk or
the assembled skills page, and `get_doc` asks it rather than branching.

### Where the "do I need this?" rule lives

`Skill.required_by(automaton, sources)` is each skill's own answer, not a
table somewhere: `mail` says yes when some action's task contains
`task.send_mail`, `whatsapp` when it contains `task.whatsapp`. The build
asks the *published* automaton, so a draft never constrains a build, and
the Build view starts from the smallest build that still runs the
project — everything not required defaults to off. The client posts the
**excluded** list rather than the included one, so a client that has
never heard of a skill can never drop one by accident.

`project.services` is the other half, on the sending side: a project may
declare a service `required`, `optional` or `disabled`, and `disabled`
bounces a message without ever touching the registry. "The operator left
this out of the build" and "this project said no" then reach the producer
as the same answer.

### What guards the seam

`backend/tests/test_wiring_contract.py` holds the composition root to its
promise: `controller.py` may not name a controller that a build could
leave out. Everything else arrives through `POINT_HTTP_CONTROLLERS`. It
is the backend twin of the frontend contract test described below, and it
exists for the same reason: an import that a build would break must fail
here, in a test, rather than at a customer's site.

### Two asymmetries to keep in mind

**Key is not package.** `avance_platform` declares `key = "platform"`, so
its routes are `/api/skills/platform/…` while the directory a build drops
is `src/avance_platform`. Anything translating between the two — notably
the frontend copy step below, whose directories are named by key — must
go through `skills.installed()` rather than assume they match.

**Installed is not enabled.** A skill's package being present and its
`.config.yml` section being configured are different facts. The first is
the build's decision and cannot change at run time; the second can. The
flags a skill contributes to `POINT_API_STATE` answer the second question
only, and the frontend needs both answers.

### Open debt

- Not every route has reached its skill yet — the migration to
  `/api/skills/<key>/…` and `/api/core/…` is in progress.
- The build copies `backend/` and nothing else; see the last section.

## Frontend — the same shape, one skill at a time

Every skill with a frontend now has one, on the same shape as its
package: `build`, `testing`, `talk`, `listen`, `whatsapp`. What is not
done is the build step — no build copies the frontend yet. The layout
mirrors the backend one-for-one:

```
frontend/src/skills/<key>/
  index.js            the manifest: the analogue of skill.py
  components/         its views, panels, tabs
  api.js              its own routes
  tests/              its tests, and nobody else's
  README.md           its own documentation, which leaves with it
```

`frontend/src/skills/registry.js` discovers manifests with
`import.meta.glob('./*/index.js', { eager: true })` — the analogue of
pkgutil. A directory that was not copied contributes nothing, and the
bundler never notices it was ever a possibility.

### The three invariants

1. **No core → skill import.** No file outside `src/skills/<key>/` may
   import anything inside it; `registry.js` is the single exception, and
   its only reference is the glob literal. Skill → core is fine (the core
   always ships). **Skill → skill is forbidden**, including indirectly.
2. **No skill name in the core.** The core never names a key: not in an
   identifier, a string literal, a route, a comment, or a line of docs.
   If the core needs the name, an extension point is missing.
3. **Nothing outside the directory belongs to the skill.** A component
   used only by one skill lives in that skill, however generic it looks.
   Leaving it in the core would ship a part of the product nobody bought.

### Extension points

The core renders what the registry gives it and knows nothing about who
contributed it. Declarative points carry data and components —
`pushedViews`, `projectActions`, `projectModes`, `servicesTabs`,
`chatInputControls`, `profileFields`. Where a skill must *act* rather
than render, the manifest contributes an object with named methods and
the core calls the method by name, the same shape as the backend Bus —
never a registered callback.

An extension point asks a skill only what the skill knows about itself.
The test mode briefly had to declare which chat skin the editor should
show while it was open — a decision belonging to the editor, with the
skill left restating the default. If a contribution field would read the
same for every skill that ever fills it, it belongs to the core, and the
core should carry it as its own default.

One rule the frontend has that the backend does not need: **a core module
low in the import graph must not import the registry.** The registry pulls
in every skill, and a skill reaches back into the core, so importing it
from something like the chat store closes a cycle. Where the core has to
announce something, it announces to a leaf module (`messageNotifier.js`)
and whoever boots the app hands that module the observers the registry
collected.

A skill's UI appears only when both are true: its directory is compiled
in, and the running backend reports it in `GET /api/skills`. The first is
the truth; the second only degrades a frontend that shipped with more
skills than its backend.

## What catches a mistake, and what does not

Measured on a real worktree, with a throwaway skill directory deleted the
way a build would delete it:

| case | result |
|---|---|
| build with the skill present | ok — its code is in `dist/` |
| `rm -rf src/skills/<key>` then build | **exit 0**, and nothing of it remains in `dist/` |
| full Vitest suite after that deletion | passes; the skill's tests left with the directory, with no config to keep in sync |
| a core file still importing the deleted skill | build **fails**, `UNRESOLVED_IMPORT`, exit 1 |
| a core *test* still mocking a deleted skill module | **passes silently** |

A third rule guards what neither of those can see: every core module must
be reachable by following imports from `main.js` and from the skill
manifests the registry's glob loads. A module nothing imports is dead
weight a build still copies — and that is how a skill's code comes back
into the core without anyone importing it, as `src/mic.js` did when one
session restored it while another was moving it into `skills/listen/`.
Duplication is the special case: the copy too many is the one nobody
imports. A `vi.mock` deliberately does not count as an import, or a mock
left behind by a refactor keeps a dead module looking alive — which is
exactly what hid that file for an afternoon.

So the worst violation is caught by the build itself and can never reach
a customer. The last row is what a contract test exists for
(`frontend/tests/skillBoundaries.test.js`): a leftover reference inside a
core test fails nothing at run time, and neither does a skill name left
in a string literal. That scan must be targeted — an exact quoted key, an
`/api/skills/<key>/` route, a `skills/<key>` path — never a whole-word
match, since the core legitimately says "build" in `buildTimeline` and
prose, and `'whatsapp'` also names a login provider that is not the skill.

## What a build delivers

`build/backend_copy.py` runs one named step per visible phase, and two of
them are the delivery:

- **Copying the backend** copies `backend/` with an ignore that drops, at
  the `backend/src/` level only, the directories named in
  `excluded_skills`, then prunes `requirements.txt` of the pip lines those
  skills declared.
- **Building the frontend** (`build/frontend_copy.py`) copies `frontend/`
  without `node_modules`/`dist`, removes `frontend/src/skills/<key>` for
  every excluded skill, and then reads back everything it copied: a source
  file still naming a dropped skill fails the build rather than shipping.

That second step translates **package → key** through `skills.installed()`
rather than assuming they match, because `avance_platform` declares
`key = "platform"`. It prunes after copying rather than through an ignore
function for the same reason — the translation needs the roster.

The read-back asks the roster, not the directory listing: **owning no
`frontend/src/skills/<key>/` is not permission to stay.** The backend
package left either way, so a core file still writing `/api/skills/<key>/`
is a delivery whose every call 404s. Deriving the names to search from
"what we managed to delete" is how a build once shipped a frontend calling
a `platform` that was not there — the directory did not exist, nothing
was deleted, and nothing was looked for. One substring, `skills/<key>/`,
covers both the import path and the route.

`platform` is the case that still has no answer: a build may drop
`avance_platform`, but the authoring frontend it serves — the editor,
settings, the app store, login, labeling — is spread across the frontend
core rather than gathered under `src/skills/platform/`, so no build can
drop it yet. The build now says so and fails; the split is open work.

What it deliberately does not do is compile the frontend. The delivery is
pruned source, built where it is deployed exactly as the Dockerfile
already builds it (`npm ci && npm run build`). Compiling here would put an
`npm ci` inside every build, minutes of it, and would make a build require
node in an environment that otherwise needs none.

The last step runs the built backend's own suite — the tests that came
with what was actually copied.

## Still open

- Serving the delivered frontend is the deployment's business: nothing
  generates an nginx config per build. The report says where the pruned
  source landed.
