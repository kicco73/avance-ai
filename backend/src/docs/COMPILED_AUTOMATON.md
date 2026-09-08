# Compiled automaton

How a project stops being YAML interpreted at run time and becomes Python,
and what had to change in the platform to make that possible without
forking it.

## Why

The end goal is a stripped-down backend: one product, built from the
features and skills it actually enables, with nothing else compiled in. A
project's automaton is the first thing in the way, because today every
behaviour a project declares — when a transition fires, what it writes to
`env`, what an on-exit line does, what a task runs — is *text*, parsed and
evaluated by `simpleeval` on every turn. A product shipped that way still
carries the whole interpreter, the YAML builder and the design-view
machinery, whether it uses them or not.

The instrument is a compiler: one project in, one Python package out. The
constraint that shaped everything below is that its **impact radius must
be as small as possible** — the platform bends to make compilation cheap,
rather than the compiled artifact growing until it can impersonate the
interpreted one.

## The criterion

`Automaton` used to be one 756-line class doing four different jobs:

1. **Data** — the states, actions, signals, reactions, env keys, sources,
   prompts and project identity a project declares.
2. **Seams** — the four places where an expression is actually evaluated.
3. **Derived answers** — questions about the project whose answers are
   fixed when `index.yml` is written, but which were re-derived by
   re-parsing the expression text on every call.
4. **Platform contracts** — payload serialization for the API,
   introspection for the design view and metrics.

Only (2) has to become generated code. (1) is emitted as literals, (3)
must become data, and (4) is written once and serves both kinds of
automaton.

The rule that partitions the class, and stays checkable afterwards:

> Nothing in the core may parse expression text at run time.

```sh
grep -n "TriggerExpressionAnalyzer\|simpleeval" src/automaton/core.py
```

Every hit must be inside one of the four seams. Anything else that needs
the analyzer is either a seam in disguise or a derived answer that belongs
in `analysis.py`.

## Module layout

| Module | Holds | Depends on |
| --- | --- | --- |
| `automaton/model.py` | `Action`, `State`, `Signal`, `Reaction`, `EnvKey`, `Source`, `MemoryArchive`, every `*Payload` type | nothing but dataclasses |
| `automaton/analysis.py` | the static analysis of a project's own expression text | `TriggerExpressionAnalyzer` |
| `automaton/core.py` | `CoreAutomaton`: data, lookups, derived answers, the four seams | `model`, `analysis`, `scope`, `simpleeval` |
| `automaton/payloads.py` | `PayloadsMixin`: API serialization, `manual_actions_for` | `model` (type-only) |
| `automaton/introspection.py` | `IntrospectionMixin`: design-view and metrics questions | `analysis` |
| `automaton/automaton.py` | `class Automaton(CoreAutomaton, PayloadsMixin, IntrospectionMixin)`, plus a re-export of everything above | all of them |

Two properties hold and are worth re-checking after any change:

```sh
# the core must import nothing from the platform, and none of the mixins
grep -n "^from\|^import" src/automaton/core.py
```

`automaton.py` re-exports every name that used to live in it, so no import
anywhere else in the codebase had to move.

The mixin split is the mechanism, not decoration: a compiled product that
serves no design view composes `CoreAutomaton` without `PayloadsMixin`,
one with no metrics or Inspector drops `IntrospectionMixin`. Impact radius
and feature-flag modularity are the same lever.

## The four seams

These are the whole of what a compiled automaton replaces.

| Seam | Evaluates |
| --- | --- |
| `_eval_trigger` → `evaluate_triggers_action` | an action's `trigger` expression |
| `eval_action_env` | an action's `env:` expressions |
| `eval_action_on_exit` | an on-exit script's `env.<key> = …` lines and bare `chat.*` calls |
| `render_task` → `render_task_script` | a `task:` script |

Two properties of `_eval_trigger` any replacement must preserve: a trigger
whose referenced signals are not computed yet returns `False` silently,
and any other failure returns `False` with a warning rather than
propagating.

All four are dispatched polymorphically from their call sites, so
overriding them in a subclass is enough — no caller branches on which kind
of automaton it has. Two call sites had to be fixed to make that true
(see *Platform changes* below).

## Derived answers

`declared_env_key_names`, `triggerable_signal_names`, `triggers_reference`
and `all_triggerable_signal_names` used to call `TriggerExpressionAnalyzer`
on every invocation — `tracking_processor.py` calls the first once per turn
and the second up to three times per turn, `chat_service.py` the first once
per turn. Their answers are fixed when the project is written.

They are now computed on first use and kept, with the parsing itself in
`analysis.py`. The three parts are cached independently, on purpose:
`declared_env_key_names` swallows a malformed on-exit and the others do
not, so bundling them would have moved where an exception surfaces.

This is not a performance change worth claiming — measured on the
*Aprendr català* sample, `declared_env_key_names` went from 0.35 ms to
0.0001 ms per call, which is noise next to an AI call. It matters because
a compiled automaton has no expression text to re-parse, and because the
platform should not depend on its own source being available at run time.

The caching relies on an `Automaton` being immutable after construction.
The platform already depended on that — `AutomatonLoader` caches one
instance per `(project_id, revision)` and shares it across requests — but
nothing enforces it. Making `State` and `Action` frozen would.

## The compiled package

```
<name>/__init__.py   every State/Action/Signal/Reaction/EnvKey/Source as a
                     literal constructor call, and AUTOMATON, an instance
                     of CompiledAutomaton
<name>/prompt.py     every model-facing text as a named constant
<name>/data/         the project's archive files, verbatim
```

Built by `backend/bin/compile_automaton.py`:

```sh
python bin/compile_automaton.py <project_dir_or_zip> --automaton-module <name>
python bin/compile_automaton.py <project_dir_or_zip> --automaton-module <name> --verify
```

The package is written under `backend/src/<name>/` so it is importable as
a plain top-level package, beside `main.py`.

**Emission is driven by `dataclasses.fields()`**, never by a hand-written
list of field names: a field added to `State` or `Action` is emitted
automatically, and a field whose *shape* the compiler has not been taught
about raises rather than being guessed at.

**`prompt.py`** holds `general_prompt`, each state's `contextual_prompt`,
each signal's and reaction's `definition`, and each env key's and source's
`ai_definition`. These are the part of a compiled product a human still
reads, tunes and translates, and the part that changes without changing
behaviour — editing one is a one-file diff instead of a diff inside
generated control flow. It is generated like everything else: recompiling
overwrites it, and nothing stops anyone from editing it in the meantime.
`fixed_message` stays inline; it is a canned reply for the user, not
something the model reads.

**`data/`** holds the archive files, read back at import time through the
platform's own `ArchiveResolver.convert_contents_to_archives`, so whether
an archive is text or base64 and what its media type is stays decided in
one place for both kinds of automaton. `index.yml` is *not* shipped — it
has been compiled away in full, and a copy would only be a stale
duplicate. A project that declares `index.yml` as an attachment is refused
at compile time rather than losing it silently at run time.

Note that an attachment is looked up by the archive's real path, not by
the name the project declared for it: `ArchiveResolver` resolves a bare
`general_prompt.txt` against a file stored at `behaviour/general_prompt.txt`.

**`--verify`** re-imports the package it just wrote and compares it,
field by field, against the automaton the ordinary `AutomatonBuilder`
builds from the same project: every state, action, signal, source, env key
and reaction, plus the derived answers.

## Selecting a compiled automaton at run time

```yaml
project-service:
  compiled-automaton: <package name>   # default: absent
```

`main.py` reads it and builds either the ordinary `AutomatonLoader` or
`CompiledAutomatonLoader` (`project/archive/compiled_automaton_loader.py`),
then injects it into `ProjectService`. `ProjectService` no longer builds a
loader itself: which one this subsystem runs on is the caller's choice.

`CompiledAutomatonLoader` serves one pre-compiled package and nothing
else. It has no revisions and no cross-project family scan, so the methods
that exist only for those are fixed answers rather than caches.

## Platform changes made for this

Every change to pre-existing platform code carries a marker at the change
site:

```python
# XXX Compiled automaton requirement - do not touch.
# XXX <one line of why>
```

The substantive ones so far are all the same defect: a method dispatched
through the hardcoded class name `Automaton`, where no other implementation
could ever answer.

- `tracking/actuators/actuator_set.py` — `Automaton.render_task(...)` →
  `scope.automaton.render_task(...)`
- `tracking/actuators/action_task.py` — `Automaton.render_task_script(...)` →
  `scope.automaton.render_task_script(...)`; `EvaluationScope` already
  carries the automaton, `state_key` and `action_name` (preserved across
  `for_task`), so a compiled automaton can resolve which task this is
  without re-parsing `script`
- `automaton/payloads.py` — `get_state_payload` called
  `Automaton.get_action_payload`, which a mixin cannot resolve

None of them changed a signature or added a parameter.

## State of the work

Done:

- the split into `model` / `analysis` / `core` / `payloads` /
  `introspection` / `automaton`
- derived answers computed once instead of per call
- the three dispatch fixes above
- `project-service.compiled-automaton` and `CompiledAutomatonLoader`
- the compiler emitting data, prompts and archives, verified identical to
  the interpreted automaton on the *Aprendr català*, *Concierge*,
  *Drogodependencia*, *Hello world*, *Lluna*, *Metrics Playground
  (states)* and *TTM prototype* samples

Not done yet:

- **compiling the seams.** A generated automaton currently inherits all
  four, so triggers, `env:`, on-exit and task still run through
  `simpleeval` on the text each `Action` carries. This is the next step,
  and the only one that actually removes the interpreter from a product.
- `task.defer(lambda: …, when)`, which hibernates a *fragment* of a task
  script and needs a generated function per defer site.

Open points:

- **`Action` survives as a data object.** An earlier design folded actions
  into their state as methods and dropped `Action` as a run-time type;
  keeping it is a regression accepted to keep moving, because
  `get_action_payload`, `move` and `manual_actions_for` all read its
  fields. To revisit.
- **`State`/`Action` are not frozen**, so the immutability the caching now
  relies on is convention rather than structure.
- **The generated package imports `automaton.builder.archive_resolver`**,
  which lives in the builder chain a stripped product is supposed to drop.
  It is a small pure utility (extension → media type, base64); moving it
  out of `builder/` would cut the dependency.
