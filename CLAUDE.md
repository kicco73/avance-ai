# Working in this repo

## Answers

Keep them very short. Answer what was asked and stop. No preamble, no recap
of what you just did, no restating the question. A few lines is normal; a
wall of text is not. Detail belongs in the code and in `backend/src/docs/`,
not in the reply.

The conventions live in `backend/src/docs/`, one file per subject, and they
are written to be read before the code they describe:

- `TESTS.md` — where a test lives (with the code it tests), the markers, and
  what the suite costs
- `BUS.md` — `system/bus.py`, the seam a package uses to reach something it
  must not name; any change to the bus updates this file in the same diff
- `SKILLS.md`, `BUILDING_PLATFORMS.md` — what a skill is, and what a build
  leaves out
- `PROJECT_SPECS.md`, `SESSION_SPECS.md`, `METRICS.md`, `BENCHMARK.md`,
  `SKIN_SPECS.md`, `COMPILED_AUTOMATON.md`, `TERMS.md` — the domain

`README.md` covers architecture, install and configuration.

## Comments

Comments are not a source. To find out what code does, run it, read what it
calls, or write a test that fails. A comment in `chatStoreFactory.js` said
`result.reply` is «always empty for a live turn», twenty-five lines above the
code that reconciles the streaming bubble against `result.reply[0]`; believing
it produced a bug.

Do not add new ones. Why a change was made belongs in the commit message, a
contract belongs in `backend/src/docs/`, and a behaviour that must not regress
belongs in a test. A false comment gets deleted, not rewritten.

## Tests

```
cd backend && python3 -m pytest
```

`conftest.py` records every test's outcome and duration in
`backend/test_stats.json`. To see what the suite costs, or what you would
delete to make it shorter:

```
cd backend
python3 bin/filter_tests.py test_stats.json -d
python3 bin/filter_tests.py --help
```

It collects the suite first and counts only the tests a real run executes —
the statistics alone would include tests that never run, and the suite would
look about twice as long as it is. `TESTS.md` explains that, and why the
deletion list the tool ranks is a heuristic about cost that has to be read
against what each test defends.

## Running the tests

Run the **targeted** tests for what you touched. The full suite runs **once**,
as the final gate before a commit — not before every push, and never twice on
the same tree.

A full run is not a substitute for working out what a change can break. A
comment cannot break 1500 tests. A rename inside `testing/` needs
`src/testing/tests/`. If you cannot name what a full run would tell you that a
targeted one would not, you do not need it.

If someone tells you a run is unnecessary, stop running it. Do not make it
faster and run it anyway.

## Working with the people here

Be collaborative, not defensive. When someone says you got something wrong,
check and fix it — do not build a case for why you were right. Relitigating
who said what is never the work; the number being correct is.

Numbers in this codebase are derived, not guessed. A timeout, a threshold, a
floor: each one is computed from something real and says what, so the next
person can tell whether it still holds. A constant that can only be defended
after the fact is a constant that is wrong.
