# Working in this repo

## Answers

Keep them very short. Answer what was asked and stop. No preamble, no recap
of what you just did, no restating the question. A few lines is normal; a
wall of text is not. Detail belongs in the code and in `backend/src/docs/`,
not in the reply.

**A document lives with the code it describes**, the way a test does. The
core's own are in `backend/src/docs/`, one file per subject, and they name
no skill:

- `TESTS.md` — where a test lives, what a core test may assume about the
  tree it runs in, the markers, and what the suite costs
- `BUS.md` — `system/bus.py`, the seam a package uses to reach something it
  must not name; any change to the bus updates this file in the same diff
- `API.md` — the HTTP surface, and how a failure becomes a status
- `SKILLS.md` — what a skill is; each installed one writes its own section
- `PROJECT_SPECS.md`, `METRICS.md` — the domain

Everything else belongs to a skill and is in `backend/src/<package>/docs/`,
where it leaves with its package:

- `SKILL.md` — every skill has one: what it does, in the customer's words
- `avance_platform/` — `SESSION_SPECS.md`, `SKIN_SPECS.md`,
  `MARKDOWN_GUIDE.md`
- `build/` — `BUILDING_PLATFORMS.md`, `COMPILED_AUTOMATON.md`
- `testing/` — `BENCHMARK.md`
- `whatsapp/` — `WHATSAPP.md`; `webchat/` — `WEBCHAT.md`

A skill's document never mentions another skill. Where two need to say
something about one subject, each writes its own half and
`system/doc_catalog.py` assembles what this build installed — that is what
`/api/core/docs/{slug}` serves, and why a project format whose services
each document their own `task.*` call reads complete in every build.

**A `docs/` directory is never delivered**, at any level, so nothing a
visitor or the running product needs may live in one. Text the product
serves lives with the code that serves it — the Terms of Service are
`auth/terms.md`, beside the route — and only reference material an author
reads in the editor is allowed to degrade when a build drops its
directory.

`README.md` covers architecture, install and configuration.

## Where a thing belongs

**The core is the last fallback, never the first.** Before writing a feature,
work out what it logically belongs to — a skill, a channel, a package that
already owns that subject — and put it there. The core is what is left when
nothing else can own it, not the drawer everything is dropped into because it
is reachable from everywhere.

The pull towards it is constant and it always looks reasonable: the core can
see every package, so anything put there compiles and every caller is one
import away. That is the symptom, not the argument. A build that leaves a
skill out has to lose that skill's behaviour with it; behaviour that ended up
in the core stays, orphaned, in a product that has no use for it.

Opening a conversation is the worked example. The core resolves which session
you are in and announces it; whether the conversation should then *speak* is
what a chat does when it opens, so it lives in `webchat/`, reached by a Bus
event (see `backend/src/docs/BUS.md`, `session.opened`). It was in the core
first, because that is where the listener already was, and it was wrong there:
the phone channel opens its conversations itself and would have inherited a
decision it does not make.

## Comments

Comments are not a source. To find out what code does, run it, read what it
calls, or write a test that fails. A comment in `chatStoreFactory.js` said
`result.reply` is «always empty for a live turn», twenty-five lines above the
code that reconciles the streaming bubble against `result.reply[0]`; believing
it produced a bug.

Do not add new ones. Why a change was made belongs in the commit message, a
contract belongs in `backend/src/docs/`, and a behaviour that must not regress
belongs in a test. A false comment gets deleted, not rewritten.

## What a test may look at

A test verifies behaviour and the i/o contract: what goes in at a public
entry point, what comes out of a public observation. Never how the thing is
made. A private method called from a test, a private attribute read to
assert, a monkeypatch on an internal collaborator — each one fails on a
rename and holds when the product breaks.

Drive the unit the way production drives it, and observe what a caller can
observe: the frame on the socket, the message on the Bus, the row in the
database, the value the public method returned. `push_event()` returning
False *is* «nobody is registered»; reading `_connections` to say the same
thing asserts the shape of a dict.

The failure this prevents is not hypothetical.
`test_metrics_are_never_computed_when_no_trigger_references_one` patched
`tracking_service._metrics`, but `_process` builds a `MetricService` of its
own and uses that one — so the assertion passed whether the gate worked, was
inverted, or was deleted. A test that cannot fail costs a full run and
defends nothing.

Three things are not reach-in, and are wanted: a fake injected at a port the
constructor already takes; an abstract method implemented by a test subclass
(`Job._prepare` is a contract of extension); and a test whose subject really
is the source tree (`test_skill_boundaries.py` reads AST because the AST is
what it defends).

Where no public observation exists — a performance property, a concurrency
window that must be held open — keep the seam and say why in one line. That
is a decision, not an accident.

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
