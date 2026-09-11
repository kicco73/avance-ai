# Where a test lives

One rule: **a test lives with the code it tests.** A skill's tests are
inside the skill's own package; everything else is core and lives in
`backend/tests/`.

```
backend/
  conftest.py            the shared harness — fixtures, fakes, helpers
  pytest.ini             testpaths = tests src
  tests/                 the core's own tests
    turn_harness.py      a turn engine standing on its own, for direct drives
  src/<skill>/tests/     that skill's tests, and nobody else's
```

## Why a directory and not a marker

A build leaves a skill out by not copying its directory (see
`system/skills.py`). Nothing else records the choice — there is no
manifest, no flag, nothing to read back at run time — and the build's
last step runs the built backend's own test suite (see
`build/backend_copy.py`).

A marker cannot do this job. pytest *imports* every test module before it
applies `-m`, so a deselected module that imports `whatsapp.whatsapp_service`
is still a collection error in a build without WhatsApp. A directory that
was never copied is not there to import. The same absence that removes
the code removes its tests, with nothing to keep in sync.

## The three kinds of dependency, and what each does about it

**Imports the skill's modules.** The test belongs to that skill and lives
in its `tests/` package. This is the hard case and the only one a marker
could not have handled.

**Needs a second skill as well.** `pytest.importorskip("talk.talk_service")`
at the top, so the module still collects where that skill is absent and
says why it did not run. WhatsApp's voice tests do this for `talk` and
`listen`.

**Reaches a skill only through its HTTP surface.** Nothing to import, so
nothing breaks at collection — but the routes are not there to answer.
The core harness abstains for it: `conftest.installed_skill(package)`
skips when the package is not in this build, and the two helpers that
need one call it — `hello_project` (the authoring surface uploads and
publishes the project) and `chat_socket` (webchat is what answers a turn).
A core test that reaches a skill's surface some other way calls it itself.

## The harness composes the way the system composes

The `app` fixture builds the core, offers it at `bus.POINT_CORE_SERVICES`,
and then starts *whatever skill is on disk* — it names none of them. That
is what lets the same harness run inside a pruned build: the package is
absent, so its routes, its services and its tests are absent with it, and
no fixture has to be told.

A test module never imports another test module. Anything two of them
share is core, and core belongs in `conftest.py` or `tests/turn_harness.py`.

## One thing pytest gets wrong on its own

`build/` is a skill here, and pytest's built-in `norecursedirs` excludes a
directory of that name — so its tests collect when you name the path and
vanish from a full run. `pytest.ini` therefore sets `norecursedirs`
explicitly: pytest's own default with `build` taken out.

## Markers

`regression`, `contract`, `needs_review`, `slow` — see `pytest.ini`.

`spawns_a_build` is the one with teeth: it marks a test that builds a
backend copy and runs its tests. A build's own test run deselects it,
because a build that builds a backend that builds a backend does not end.
