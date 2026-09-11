# Building a platform out of skills

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
state (`POINT_API_STATE`). The core assembles what arrived and names
nobody. `ui_label`/`ui_description` travel the same way, so even the
*words* on screen for a skill come from the skill.

Two skills can even negotiate without meeting: `product` stands down from
`POINT_AUTOMATON_LOADER` when `build` is installed, because a backend
that can compile should decide per project and revision. Neither imports
the other; the point they both answer composes them.

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

- Compile-on-publish is still wired on the frontend
  (`useProjectAdminActions.js` calls the build skill after a publish),
  which is core driving a skill by name. It belongs on the Bus: platform
  publishes, `build` subscribes, and the response carries what was built.
- Not every route has reached its skill yet — the migration to
  `/api/skills/<key>/…` and `/api/core/…` is in progress.
- The build copies `backend/` and nothing else; see the last section.

## Frontend — the same shape, not yet built

Today the frontend has none of this: `App.vue` statically imports every
view, `EditProjectView` imports the test panel, `ServicesView` carries a
literal list of skill ids, and no build ever copies the frontend. The
target mirrors the backend one-for-one:

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

So the worst violation is caught by the build itself and can never reach
a customer. The last row is what a contract test exists for
(`frontend/tests/skillBoundaries.test.js`): a leftover reference inside a
core test fails nothing at run time, and neither does a skill name left
in a string literal. That scan must be targeted — an exact quoted key, an
`/api/skills/<key>/` route, a `skills/<key>` path — never a whole-word
match, since the core legitimately says "build" in `buildTimeline` and
prose, and `'whatsapp'` also names a login provider that is not the skill.

## What the build still has to learn

`backend_copy.py` copies `backend/` and nothing else. Shipping a
frontend per customer needs one more step in `STEPS`:

1. copy `frontend/`, ignoring `node_modules`, `dist`, `.vite`;
2. translate package → key — `excluded_skills` names packages
   (`avance_platform`), the frontend directories are named by key — then
   remove `frontend/src/skills/<key>` for each excluded key;
3. `npm ci && npm run test && npm run build` inside the copy, the
   frontend half of "a build runs its own tests";
4. point the built `nginx.conf` at `<build>/frontend/dist`;
5. check the produced `dist/` for any trace of an excluded key — that
   directory is what actually reaches the customer.

`npm ci` adds one to three minutes and needs node in the build
environment, which the backend build does not otherwise require.
