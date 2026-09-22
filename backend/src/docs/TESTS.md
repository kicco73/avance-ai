# Where a test lives

One rule: **a test lives with the code it tests.** A skill's tests are
inside the skill's own package; everything else is core and lives in
`backend/tests/`.

```
backend/
  conftest.py            the shared harness — fixtures, fakes, helpers
  pytest.ini             testpaths = tests src samples
  tests/                 the core's own tests
    turn_harness.py      a turn engine standing on its own, for direct drives
  src/<skill>/tests/     that skill's tests, and nobody else's
  samples/tests/         what exercises a sample project, and leaves with it
  src/docs/tests/        what asserts the docs themselves, same reason
```

`samples/` is a directory a build never copies, so it is a switch exactly
as a skill's package is: a test that needs `samples/projects/<something>.zip` lives in
`samples/tests/` and is simply not collected where the samples are not.
A test that only needs *a project* never touches them — `hello_project`,
or `conftest.new_project(client)` for a second one, both created through
"New project", whose template travels with the authoring surface.

## Why a directory and not a marker

A build leaves a skill out by not copying its directory (see
`system/skills.py`). Nothing else records the choice — there is no
manifest, no flag, nothing to read back at run time — and the build's
last step runs the built backend's own test suite (see
`build/backend_copy.py`).

A marker cannot do this job. pytest *imports* every test module before it
applies `-m`, so a deselected module that imports a skill's own service is
still a collection error in a build without that skill. A directory that
was never copied is not there to import. The same absence that removes
the code removes its tests, with nothing to keep in sync.

## The three kinds of dependency, and what each does about it

**Imports the skill's modules.** The test belongs to that skill and lives
in its `tests/` package. This is the hard case and the only one a marker
could not have handled.

**Needs a second skill as well.** `pytest.importorskip("<package>.<module>")`
at the top, so the module still collects where that skill is absent and
says why it did not run. A channel's voice tests do this for whatever
speaks and whatever transcribes.

**Reaches a skill only through its HTTP surface.** Nothing to import, so
nothing breaks at collection — but the routes are not there to answer.
The core harness abstains for it: `conftest.installed_skill(package)`
skips when the package is not in this build, and the two helpers that
need one call it — `new_project`, which `hello_project` is the fixture
form of (the authoring surface creates, activates and publishes the
project) and `chat_socket` (a channel is what answers a turn).
A core test that reaches a skill's surface some other way calls it itself.

## What a core test may assume about the tree it runs in

The build copies whole directories, so a test **file** is atomic: it goes
into every delivery or into none, and there is no third outcome. That
single fact decides everything below, and the build's last step — running
the copied suite — is what turns a wrong decision into a red build rather
than a surprise at a customer's site.

**A file belongs entirely to one side.** A test that drives
`/api/skills/<key>/…` belongs in `src/<key>/tests/`, named after the
controller it drives, and it leaves with the skill. The same file in
`backend/tests/` ships everywhere and returns 404 wherever the skill was
not copied. A file that mixes the two is the bug: split it, move the
skill's half, and leave the core's half where it is. `backend/tests/`
holding a test of one skill's own settings route is what a build without
that skill fails on, and the fix is never to delete the test, mark it, or
teach it to tolerate a 404.

**A core test may not assume the whole tree.** It runs against whatever
was built, so anything it knows about the source must be derived from
what is installed, not written down:

- Parametrise over `skills.discover()` rather than naming keys. «these
  two are on the page, those two are not» is a test of the tree it was
  written in; «each installed skill, copied alone, produces exactly its
  own page» is the same claim, stronger, and true of every build.
- Never assert a size taken from this tree. A floor of «more than 100
  frontend calls» says nothing in a build that ships forty of them —
  count what the source itself offers instead, and compare the two
  numbers: every `${API_URL}` written must be one the scan read.
- A guard that derives its subject can become vacuous when the subject is
  empty. Say out loud that something was found, or the test passes by
  finding nothing.

**No core file may name a skill.** A table in `backend/tests/` keyed by
`src/<package>/tests/…` hands every customer the list of packages they
did not buy, and it rots besides: the file it names leaves with its skill
while the entry stays behind, so the table needs an exception for
«absent», which is the same as no rule at all. Let the file that needs
the exception carry it — that is what `REACHES_INTO` is — and a
declaration leaves with the test that wrote it.

The shape of the mistake is always the same, and it is worth recognising
before writing the code: a core test that wants to ask *whether this
build has X* is a test that is in the wrong file. `installed_skill` is
for the few places where a core behaviour is genuinely observed through a
skill's surface — a chat turn needs somebody to answer it — not a way to
keep a skill's test in the core.

## The harness composes the way the system composes

The `app` fixture builds the core, offers it at `bus.POINT_CORE_SERVICES`,
and then starts *whatever skill is on disk* — it names none of them. That
is what lets the same harness run inside a pruned build: the package is
absent, so its routes, its services and its tests are absent with it, and
no fixture has to be told.

A test module never imports another test module. Anything two of them
share is core, and core belongs in `conftest.py` or `tests/turn_harness.py`.

## Time a test moves itself

A wait in the product — a stream deadline, a `sleep`, a retry backoff —
is a timer on the event loop, and a test of one has two bad options: run
it against real seconds, or shrink the number until the test is fast and
no longer says anything about the number the product ships with.
`tests/virtual_clock.py` is the third: `VirtualClockLoop` is an event
loop whose `time()` is a value the test owns, so the product runs with
its real 10 s and the test pays nothing for it.

```python
with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
    runner.run(scenario(runner.get_loop().clock))
```

`clock.advance(seconds)` fires every timer due in that span, each at the
instant it was scheduled for and in order, and returns once the loop has
nothing left to run — so an assertion after `advance(9.9)` sees the
world at 9.9 s and one after a further `advance(0.1)` sees it at 10.0 s.
`clock.settle()` is `advance(0)`: let everything already runnable run.
The test drives the loop, so it never `wait_for`s anything: it moves
time, then reads what was published.

`tests/scripted_provider.py` is what such a test puts on the other end
of the call: an `LLMProvider` that does, on each call, the one thing the
test scripted — `Reply`, `RepliesAfter(seconds, text)`, `Stalled`,
`StallsAfter(prefix)`, `Trickles(words, gap)`, `Crashes(error)` — and
counts the streams the product closed before they finished
(`torn_down`). `test_stalled_reply_ends_in_time.py` replays production
session 55 with the two together.

## How a test gets a session

The same two ways a browser does, and no other: `conftest.enter_chat` and
`conftest.create_chat`. Both go through `_opening_frames`, which opens a
`chat_socket`, sends one frame — `session.enter` for the first,
`session.create` for the second — and returns everything the announcement
produced, up to and including the `state.buttons` that ends it (or the
`session.blocked` that refuses it). `enter_chat` asks for the conversation
that is open for this project, or a new one if there is none; `create_chat`
asks for a new one regardless. Either takes the kind as `session_type`, so
a test of the editor's Test chat or the App Store's preview says so and
uses the same helper.

Nothing else will do: no route resolves or creates a session (see
`BUS.md`), and reading a transcript does not open one, so a test that
skips the frame has no session to name. `conftest.session_of` reads the id
off the `session.info` frame those helpers return, and that id is what
`chat_turn` and the rest take.

## One thing pytest gets wrong on its own

One package here is named `build`, and pytest's built-in `norecursedirs`
excludes a directory of that name — so its tests collect when you name the path and
vanish from a full run. `pytest.ini` therefore sets `norecursedirs`
explicitly: pytest's own default with `build` taken out.

## Markers

`regression`, `contract`, `needs_review`, `slow` — see `pytest.ini`.

`spawns_a_build` is the one with teeth: it marks a test that builds a
backend copy and runs its tests. A build's own test run deselects it,
because a build that builds a backend that builds a backend does not end.

# What a test may look at, and what checks it

CLAUDE.md states the rule: a test drives a public entry point and observes
a public result, never how the thing is made. `tests/test_tests_stay_on_the_contract.py`
is what enforces it — it walks the AST of every test module and fails on a
test that reads or calls another object's private member.

It counted 240 such reaches across 56 files when it was written. What
survived is declared by the test that does it: a module-level
`REACHES_INTO` mapping each private member to the reason it has no public
form — a performance property nothing can observe, a concurrency window
that must be held open, a registry with no public writer. A second test
fails when a declared member stops being reached, so a declaration can
only shrink and cannot rot into a blanket.

The declaration lives in the file, not in a table in the core, for the
reason a skill's tests live in its package: a central list would name, in
every delivered build, the skills that build left out, and it rots when a
file it names leaves while the entry stays behind.

Name mangling is not what holds this line, and the repo already proves it:
`GeminiProvider` mangles `__build_contents`, and tests reached in anyway as
`provider._GeminiProvider__build_contents  # type: ignore`. Mangling buys a
deterrent and a visible diff. This test is the gate.

# What the suite costs, and the tool that says so

`conftest.py` keeps a record of every test in `backend/test_stats.json` — how
often it ran, how often it failed, how long it took. `bin/filter_tests.py`
reads that file and answers two questions: how long a full sweep takes, and
what you would delete to make it shorter.

```
bin/filter_tests.py test_stats.json -d
```

## The run you do while you work, and the one that gates a commit

```
python3 -m pytest -m "not slow"     # while iterating
python3 -m pytest                   # once, before a commit
```

`slow` is what `pytest.ini` says it is: a test that spawns a real subprocess
or otherwise takes several seconds. There are thirteen of them and they are
23 s of a 4½-minute suite, so the first command is the one to live in. It is
not in `addopts` on purpose — the gate has to pay them.

## What a test costs before it asserts anything

Two fixtures are most of the suite's floor, and both are per-test by design:
`app_db` gives each test a database of its own, `hello_project` a project
created and published through the real routes. Measured on this machine:
`app` sets up in ~0.2 s, `hello_project` adds ~0.25 s on top, and 290 of the
1594 tests take one or both.

`app_db` therefore copies a database instead of creating one: the schema is
built once per session in `app_db_template` and each test gets a copy of that
file. Creating it costs 92 ms, copying it 25 ms, and the difference is 19 s
over a full run. The isolation is unchanged — a copy is still a database
nobody else writes to.

This is also why the deletion list below is nearly useless under a second:
what it ranks there is the fixture, not the test. Two of the entries a
one-minute cut proposed were a two-line 404 and a three-line document read.

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
that did arrive, instead of a process that never returns. How long it waits
is a share of the other net's allowance for the very test that is running
(`conftest.turn_frame_seconds`), not a number of its own: both nets fire on
the same hang and the run dies at the second one, so the frames are saved
only if this one gets there first — which a share below one guarantees for
every test, whatever the numbers below become.

**The one that knows nothing.** Any test can stop for its own reasons, so
`conftest.pytest_runtest_protocol` arms a timer before each one and cancels
it after. On expiry it prints the node id it gave up on, dumps every
thread, and exits. The name comes first and on its own line because the
dump alone does not identify the test: this process parks dozens of
threads in scheduler and job-queue waits, and faulthandler truncates the
list before reaching the one that was running.

How long each test gets is read off `test_stats.json`, which records what
that very test has taken on previous runs (`seconds` over `runs`):

```
allowance = min(300s, max(3s, average * 20))      # 120s for a test never timed
```

The websocket deadline above is a share of this same number, so the net
that can name the frames always fires first, by construction rather than
by two constants happening to be in the right order.

A floor, because the proportion alone would give a 30 ms test 0.6s and a
loaded machine would trip it; the widest ratio ever recorded here between
a test's latest run and its own average is 8x, which leaves the floor an
order of magnitude of room. A ceiling, because the build test takes four
minutes and twenty times that is no longer a net. The traceback goes to a duplicate of the
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

And the list is not stable enough to execute anyway: asked for one minute
twice, twenty minutes apart, with a peer's run landing in between, it named
36 nodes of which 25 had changed. Around a second the ranking is measurement
noise. Everything above a second is worth 61 s in all, so "cut a minute" is
not a cut — it is the whole candidate list, contracts and concurrency tests
included.

## Open: an intermittent hang in test_reactions_end_to_end

Nobody is working on this. It is written down because it costs a whole
run when it happens and because what is known about it took two
afternoons to collect.

**What is seen.** A full-suite run stops making progress inside
`test_reactions_end_to_end.py` — observed twice on the same day, at
`test_message_list_and_reaction_endpoint_round_trip` and at the test
before it. The process stays alive with no output. Before
the watchdog existed, that meant a run that never returned.

**What is known.** It needs load: it appeared only while three or four
pytest runs were going at once, and neither full run made on a quiet
machine reproduced it. It is not a slow test being cut off — the test
averages 0.8 s and had 16.7 s. Both nets around a turn were in place and
neither named a cause, which is what the fix below was for.

**What is ready for the next occurrence.** `chat_turn_frames` now wraps
its deadline around the whole body, opening and closing the socket
included, rather than around the receive loop alone: if the hang is in the
handshake or the close, the failure arrives as an AssertionError naming
the frames that did arrive — zero of them, in that case. The watchdog
prints the node id before dumping, so the test no longer has to be
identified by counting progress dots.

**What is not known.** Whether it is a product bug or a test one. Nothing
rules out a real race in session creation or in the bus channel that a
loaded machine merely makes visible.

