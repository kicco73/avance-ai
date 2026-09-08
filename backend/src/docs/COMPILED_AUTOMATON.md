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

**`data/`** holds the archive files, verbatim. Nothing is loaded at
import: `AUTOMATON.archives_dir` points here, and the platform's own
reader opens a file when a turn actually asks for one — whether it comes
back as text or as base64, and what its media type is, decided in one
place for both kinds of automaton. `index.yml` is *not* shipped — it
has been compiled away in full, and a copy would only be a stale
duplicate. A project that declares `index.yml` as an attachment is refused
at compile time rather than losing it silently at run time.

Note that an attachment is looked up by the archive's real path, not by
the name the project declared for it: the builder resolved a bare
`general_prompt.txt` against the file stored at
`behaviour/general_prompt.txt`, and it is that path the package carries.

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

- the seams compiled: every trigger, `env:` expression, on-exit line and
  task statement emitted as a real function, the three primitives
  overridden, nothing in a generated package evaluating a string
- `bin/verify_compiled_seam.py`, which runs every expression of every
  sample through both automata on the same scope: 303 comparisons across
  8 projects, 0 divergences
- the Build view's Target step wired to a real "Local module" build
- the Sources/tool-calling skill "out": every declared source embedded as
  a project-level attachment, dropped from the states' own
  `ai-may-read-sources`/`ai-must-read-sources`/`ai-may-write-sources`,
  with a build warning saying what that costs per turn

Not done yet:

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

## Sources and attachments: one collaborator, chosen once

Both ways a project reads its own files went to the database at the
automaton's pinned revision — `AvanceArchiveSource` for `source.*`,
`AttachmentNamespace` for `attachment.read` — which is the one thing a
compiled automaton cannot do: it has no storage location.

Both now ask a `ProjectFiles` (`tracking/project_files.py`) the same two
questions: resolve a name to a stored path, and give me that file's raw
bytes and its media type. Bytes rather than text, because both callers
refuse a binary file with a message of their own and can only do that if
the media type reaches them before anything has tried to decode.

Three implementations, composed:

- `DbProjectFiles` — the Archive rows of this automaton's revision, what
  the platform has always done
- `SessionCachedProjectFiles` — a decorator, not part of the database
  implementation: the per-session frozen copy is a *source's* policy, and
  `attachment.read` never had it and still does not. It guards against a
  draft revision being rewritten while a test session runs on it, not
  against a republish, so it has nothing to do for a compiled product
- `PackageProjectFiles` — the real files under a package's own `data/`

`project_files_for(db, automaton, session_id)` is the only selection
point: an automaton with no storage location has nothing to read from a
database, whatever database it is handed. `SourceNamespace.__init__` and
`EvaluationScopeBuilder.build` call it once each; no driver ever asks.

Nothing about the URL, the project YAML or the design view changes: a
compiled Vueling Refund answers
`source.tickets_sold.value('julien.fernandez@hotmail.com', key='codice_volo')`
with `VY6008`, and `attachment.read('tickets.csv')` with the file, with
no database at all.

## The automaton no longer carries its project's files

`Automaton.attachments` held every file of the project, converted once,
in every automaton the loader kept cached. Exactly one thing read it at
run time — and only since the collaborator above existed. Before that it
was carried and never read: a build-time pool the subsets were extracted
from, kept alive for the life of the automaton for nothing.

It is gone. What replaces it is one field, `archives_dir`: None on the
platform, where `revision` already says where to read, and its own `data/`
directory in a compiled package. `project_files_for` reads that field and
nothing else to choose between `PackageProjectFiles` (real files, with a
traversal guard), `DbProjectFiles` and `NoProjectFiles` — an automaton
built in memory by a test, which has neither, now says "not found"
plainly instead of raising from inside a driver.

`EXTENSION_TO_MEDIA_TYPE` moved to `automaton/media_types.py`: the
builder that converts archives and the reader that serves them have to
agree on it, and the reader must not drag in the YAML-building chain a
compiled product does not ship.

### The declarations carry names, not files

`general_attachments` and each state's, signal's and action's own held
`MemoryArchive` objects — the file, converted, inside the automaton. They
now hold the *stored paths* those declarations resolved to, and nothing
else.

Where each half of that happens:

- **Build time verifies and resolves.** `ProjectArchives`
  (`automaton/builder/archive_resolver.py`) is the project's files as the
  builder sees them: it answers which stored path a declared name means —
  an exact match, or a unique basename — raises where the name is
  ambiguous or absent, and hands out the text of a file for the two
  checks that must look inside one (`attachment.read`'s text-only and
  size limits). It replaces the old dict of `MemoryArchive` that every
  `_build_*` method was threaded. Nothing converts a file any more just
  because a project carries it.
- **Run time reads.** `tracking/attachments.py` turns those paths into
  the `MemoryArchive` objects a turn actually sends, through the same
  `ProjectFiles` every other project-file read goes through. Text or
  base64 is decided from the media type — the same rule, in the same
  place, for both worlds.

What a file *becomes* is decided from its name and nothing else
(`automaton/media_types.py`), never from the media type the reader
reports. That is not tidiness: a stored project's Archive row carries the
type the uploader stamped on it — `text/csv` for a CSV — while a
package's `data/` carries only the file, so a reader-decided split would
send the same attachment as text in one world and as base64 in the other.
The table is the one the builder already used when the automaton carried
the converted files, so what reaches the provider is byte-for-byte what
it was. `test_attachment_resolution.py` pins the two against each other.

Resolution is a build-time question, so it gets a build-time answer: a
turn reads the path it was given and does not look for it again. That is
also what keeps the payloads honest — the design view's per-state
attachment list used to show declared names while the per-signal one
showed resolved paths, and the frontend checks both against the project's
real file list.

### The cache

Carrying every file made assembling a turn free; reading them per turn
only stays free if something remembers them. `ProjectFileCache`
(`tracking/project_files.py`) is one process-wide LRU bounded in **bytes**
— an entry count is not a bound on memory when the entries are files — and
both real readers sit behind it as `CachedProjectFiles`. Only `read` goes
through it: resolution answers a name, not a file, and the bound is about
files. A file bigger than the whole bound is served and not kept, rather
than evicting everything for something that still would not fit.

The key comes from the reader, never from the cache: `DbProjectFiles`
identifies a file as `(project id, revision, path)`, `PackageProjectFiles`
as its own `data/` path. Neither carries a slot the other leaves empty —
the packaged product has no revision, and so no field where one would go.

Its size is `chat-service: project-file-cache-bytes` (default 8 MiB),
configured once at boot from `main.py`. That section rather than
`project-service`, which does nothing in a package, or `database`, which
says where bytes are stored and not how a turn budgets them: this is a
per-turn budget beside the two token budgets already there, in a section a
compiled product still has.

Entries used to be dropped for free when the automaton holding them left
`AutomatonLoader`'s cache. Now they are dropped by the bound, plus one
explicit invalidation: `ProjectManager.finalize_update`, the single funnel
every project save goes through — a draft revision is rewritten in place,
so its number does not change when its bytes do — and the two archive
mutations that happen outside it, deleting a project and deleting a
source's own CSV.

The open point on `estimate_state_prompt` is closed the way it was put:
it takes a `ProjectFiles` from its caller (`ProjectInspector.
get_state_input_tokens`, which has the db), so the design view's per-state
estimate still counts the attachment bytes a real turn would send.

## Anomalies found on the way

Things this work surfaced that are *not* about the compiled automaton and
were left alone, recorded here so they are not rediscovered from scratch.

- **`ai-access` is documented but not implemented.** `PROJECT_SPECS.md`
  §5.3 describes a per-env-key `ai-access` field, and in one place says
  that omitting it is a build error. `AutomatonBuilder._build_env_key`
  reads only `value`, `ui-description` and `ai-definition`; the key is
  inert. What a state exposes to the model is decided by its own
  `input`/`output`. Either the docs or the builder is wrong — the *Vueling
  Refund* sample was written against the docs, which is why it declared
  fields nothing read.
- **`avance:env` does not exist.** `State.ai_may_write_sources`' own
  comment says "today, just `avance:env`, see tracking.sources.avance_env",
  and `PROJECT_SPECS.md` describes the driver. There is no such module:
  `tracking/sources/` has `avance_archive` and nothing else. A project
  declaring `url: avance:env` resolves it as an *archive* named "env",
  archives support no `update`, and the build fails with
  "ai-may-write-sources 'env' references undefined name(s):
  source.env.update". This is what kept *Vueling Refund* unbuildable.
- **`test_controller_settings_services.py::test_get_services_returns_the_configured_snapshot_verbatim`
  fails on master**, unrelated to any of this: the services snapshot does
  not include the `build-service` section the test now expects.
- **`test_all_signals_shared_observations.py::test_all_signals_aggregation_builds_each_runs_observations_only_once`
  is flaky** — observed failing once and passing five consecutive runs
  afterwards, including twice in the same subset that had failed.
- **Two of the four attachment declarations are never read.** A signal's
  own `attachments:` is documented as "sent only with the signals
  computation call" and an action's as its own; neither reaches a
  provider anywhere. The signals call builds its priming messages from
  the *state's* attachments (`TrackingProcessor.__build_turn_prompt_parts`),
  and nothing whatsoever reads `Action.attachments`. The signal one does
  reach the design view's Signals tab, as a list of filenames; the action
  one reaches nothing at all. Left as they are — they are declarations
  the YAML accepts and projects use — but a project author has no way to
  know they do nothing.
- **The design view showed two different names for the same thing.**
  `get_project_signals` listed a signal's attachments as their resolved
  stored paths, `get_project_graph` listed a state's as the names the
  YAML declared, and the frontend checks both against the project's real
  file list — so a state attachment declared by basename came back not
  editable. Both are stored paths now, as a side effect of resolving at
  build time rather than a fix aimed at it.
- **A hardcoded `Automaton.` dispatch shipped as a live bug.**
  `CoreAutomaton.render_task` called `Automaton.render_task_script`
  through a name `core.py` does not import, so it raised `NameError`. No
  test caught it because the only route there is
  `ActuatorSet.schedule_task`'s dispatcher-less fallback. Fixed, but worth
  remembering: this is the *fourth* occurrence of the same pattern, and
  the only one that was not just an obstacle to a second implementation.

## Known asymmetries of the compiled seam

Two, both declared rather than hidden:

- **A bare name is bound eagerly.** A compiled function binds every name
  its expression reads before evaluating it. If a bare name (a core
  metric, or a local a task's earlier statement assigned) were missing,
  an expression that would have short-circuited past it now fails
  instead. Core metrics are always present since `values_dict` stopped
  omitting `None` ones, so the reachable case is a task whose earlier
  assignment failed.
- **A table miss is a configuration error.** If a package is generated
  from one revision and handed text from another, the lookup logs an
  error naming the text before the seam swallows it as an ordinary
  evaluation failure.
