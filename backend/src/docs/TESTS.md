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

# What the suite costs, and the tool that says so

`conftest.py` keeps a record of every test in `backend/test_stats.json` — how
often it ran, how often it failed, how long it took. `bin/filter_tests.py`
reads that file and answers two questions: how long a full sweep takes, and
what you would delete to make it shorter.

```
bin/filter_tests.py test_stats.json -d
```

## Three traps, which are what half the options are for

**`last_seconds` is written by a full run only.** It is the field the tool
reads as a test's duration. Statistics accumulated from targeted runs give an
invented total, so a number worth deciding on comes after a full run on a
quiet tree.

The tool says so itself, on stderr, because both halves of that are readable
off the file. A full run stamps every surviving entry with the same
`last_run` and drops whatever did not run, so more than one distinct
timestamp means the last run was not a full one; and an entry with no
`last_seconds` has never been through a full run at all, its duration being
the average of its recorded runs. When neither holds, the warnings are
silent.

**A test in the statistics is not a test that runs.** `pytest_collection_modifyitems`
always deselects `spawns_a_build` (see the markers section): the entry stays
in the json, carrying durations from a deliberate `-m spawns_a_build` run.
`test_a_build_ends_by_running_its_own_suite_for_real` alone is 254 s that a
normal sweep never pays, and counting it makes the suite look twice as long
as it is. So the tool does not trust the json on its own: every run starts by
collecting the suite (`pytest --collect-only`, a few seconds) and counts only
the tests that come back. It is not an option, because a number that includes
tests nobody runs is not worth having — if the collection fails, the tool
says so and stops rather than answering with the raw sum.

**A renamed test leaves its statistics behind.** `_git_renamed_test_files()`
remaps renames, but asks git for them under `tests/` only, so a test living
inside a skill's package is not covered. `--prune-stats` drops the entries
whose node id is no longer in the source *and* that the collection did not
return. Both halves are needed now that the watchdog below reads the same
file: an entry it loses is a test that gets the allowance for an unknown one,
which for a slow test is less time than it had, so nothing a real run
collects is ever pruned.

## When a test stops making progress

A suite that hangs is worse than a suite that fails: it says nothing about
where it stopped. Two nets catch that, and they are deliberately different.

**The one that knows what it is waiting for.** `conftest.chat_turn_frames`
reads a turn off the websocket with a blocking receive; a deadline around
that loop turns a frame that never comes into a red test naming the frames
that did arrive, instead of a process that never returns. Its 30s is set
against the other net's 60s floor rather than against how long a turn takes:
both would fire on the same hang, and the run dies at the second one, so the
frames are only saved if the first gets there first.

**The one that knows nothing.** Any test can stop for its own reasons, so
`conftest.pytest_runtest_protocol` arms `faulthandler` before each one and
cancels it after: on expiry every thread's traceback is dumped and the run
gives up, pointing at the exact line.

How long each test gets is read off `test_stats.json`, which records what
that very test has taken on previous runs (`seconds` over `runs`):

```
allowance = min(900s, max(60s, average * 20))     # 120s for a test never timed
```

A floor, because a 30 ms test that is given ten minutes to hang in is a
net with a hole in it, and because the same test runs slower on a loaded
machine. A ceiling, because the build test takes four minutes and twenty
times that is no longer a net. The traceback goes to a duplicate of the
real stderr, taken at configure time — pytest captures fd 2 during a test,
and a dump written there dies with the process that was supposed to report
it.

## What the ranking means, and why it is not a list of orders

`-m <seconds>` prints the node ids to delete to reach that duration. The
order is a heuristic about **cost**, not value: never-failed, recent, long and
large first. A test written this morning fits that profile exactly, and so
does a `contract` that never failed because the rule it defends is holding.
The list is where to start reading, not what to execute.

Two defenses, on by default or nearly:

- `--min-duration` (default 1.0 s) keeps short tests out of the candidates.
  Below that you delete dozens of assertions to win a few seconds, which is
  the worst ratio on offer.
- `-x <substring>` preserves whatever matches — one test, or a whole file.
  It is what the `contract`s, the regressions and the concurrency tests need:
  what they defend is not findable again once the test is gone.

When there is not enough time above the floor to reach the target, the tool
says so instead of quietly handing back a list that falls short. The right
answer then is usually a smaller cut.
