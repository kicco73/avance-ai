# Benchmark metrics

How closely a project behaves according to **expert expectations**. The
annotations stored against a session are the ground truth; every score
below is computed from explicitly annotated evaluation points and from
nothing else.

All values are normalized to `0..100`, where **100 is perfect**. Raw
distances and errors are used internally and never exposed as a metric.

Two scopes, same definitions:

- **session** — the annotations of one session;
- **project** — every annotated session of a project, aggregated.

The implementation does not change with the scope; the scope only decides
which observations are supplied.

## 1. Observations

An evaluation point is created each time the conversation is evaluated. An
expert may annotate two things on a message.

**Expected state** (`Message.expected_state`) — the state the system was
expected to be in after that message.

**Expected signals** (`Signals.expected_values`) — a JSON object holding
*only* the signals the expert annotated:

```json
{ "problemRecognition": 80, "planConcreteness": 60 }
```

The system's own values are in `Signals.values`. A signal absent from
`expected_values` does not participate.

**Which evaluation an annotation is compared with.** The system evaluates
once per turn, after the user message or after the AI reply depending on
the project, while an expert may annotate either message. An annotation is
compared with the first evaluation made on or after the annotated message
— never an earlier one, since the system could not know what it had not
yet seen:

| Project evaluates after | Annotated message | Compared with the evaluation on |
| ----------------------- | ----------------- | ------------------------------- |
| the AI reply            | user message N    | AI reply N                      |
| the AI reply            | AI reply N        | AI reply N                      |
| the user message        | user message N    | user message N                  |
| the user message        | AI reply N        | user message N+1                |

An annotation with no evaluation after it is not compared. Two annotations
reaching the same evaluation make one comparison: for each signal, and for
the state, the later annotation wins.

**How a replay starts a session.** A replay re-runs a session from its
start, the way the engine started it. Whether the engine fired the
`init-action` comes from the session's type and the project's
`new-session-strategy`: imported, test and preview sessions always start
from it; a live session does under `restart`, and under `resume` only if it
was the user's first. When it fires, the replay's env is emptied, every
declared key is set to its type default, and the `init-action` is applied,
`on-exit` included; the replay starts in its target. Otherwise the env is
the session's own at its start, missing declared keys are set to their
defaults, and the replay starts in the session's `start_state` — the first
annotated `expected_state` only when there is none, since that is the state
expected *after* its message. All of it is written to the replay's own env
and observations, never to the session's.

**What each strategy shows the model.** `turn_by_turn` makes one call per
turn, with the history up to that turn, as live tracking does, and asks
for exactly the signals the replay's current state tracks — never those of
the annotated state or of the state the original session was in. A signal
the replay did not compute at a point has no value there and is not scored
there, as live would not have computed it. `batch` and
`batch_lite` cover several turns per call with the transcript embedded in
the prompt, each covered turn labelled `[Turn N]` right before its user
message. `batch` shows both sides in full. `batch_lite` shows both sides
too, the user's messages in full, but shortens every assistant message
longer than `2 × ASSISTANT_EXCERPT_CHARS + 1` characters to its first and
last `ASSISTANT_EXCERPT_CHARS` (100) with `…` between them — the head
carries the reaction to the user, the tail usually the question the next
message answers. It saves anything only when assistant replies are long;
with short replies it costs what `batch` does. A signal that rates the
*content* of the assistant's replies needs them whole: use `batch`.

**No turn is rated with hindsight.** Each turn is rated on exactly what
live tracking saw for it: the history up to and including its user message,
plus the assistant reply to it when the project tracks after the AI message
(live, the signals come in the same call as that reply). In a batch
transcript that is everything before the next turn's `[Turn N+1]` label,
and everything shown for the last turn of the call — which is why, when
tracking runs after the AI message, the transcript also carries the reply
to the last covered turn. `turn_by_turn` enforces this structurally: its
call never contains a later message. `batch` and `batch_lite` enforce it by
instruction only — the later turns are in the transcript so one call can
cover several, and the model is told to rate each turn as if they did not
exist yet. A model that ignores that instruction lets the rest of the
session leak backwards into earlier turns; if a benchmark shows it, the
remedies are fewer turns per call or `turn_by_turn`.

## 2. State accuracy

Whether the expected state was reached at an annotated point: `100` for a
match, `0` for a mismatch.

```text
state_accuracy = correct state annotations / state annotations * 100
```

Ten annotated states, eight correct, is `80`. Only messages carrying an
`expected_state` are included.

## 3. Signal accuracy

How close a value is to the expected one. Signals range `0..100`, so:

```text
signal_accuracy = 100 - abs(actual - expected)
```

| Expected | Actual | Accuracy |
| -------: | -----: | -------: |
|       80 |     80 |      100 |
|       80 |     70 |       90 |
|       80 |     50 |       70 |
|       80 |      0 |       20 |

The aggregate is the arithmetic mean over the annotated signals only.
Per-signal results are kept alongside it —

```text
problemRecognition    91
decisionalBalance     84
planConcreteness      63
```

— so a good overall score cannot hide one consistently poor signal.

## 4. Transition responsiveness

Correctness says nothing about **when** the expected state was reached.
Where an annotation identifies an expected transition, two independent
distances are measured between the expected and the actual point.

**Message delay** — the difference in message position, normalized
against the largest relevant message distance in the session. `0` is a
transition exactly where expected; earlier or later is an error.

**Time delay** — the difference in timestamps, bounded by
`BenchmarkConfiguration.max_session_duration_in_minutes`, the single
temporal reference. The application supplies it from:

```yaml
turn-service:
  max-session-duration-in-minutes: 60
```

With a 60-minute bound, 0 minutes → `100`, 30 minutes → `50`, 60 minutes
→ `0`. The component stays meaningful precisely because a session cannot
stay open indefinitely — no other 60-minute constant should exist in
session management.

Both are normalized to `0..100` and combined with equal weight:

```text
transition_responsiveness = (message_responsiveness + time_responsiveness) / 2
```

Both components are retained, so conversational and temporal
responsiveness can be diagnosed apart.

## 5. Benchmark accuracy

One high-level measure, the equally weighted mean of the three above:

```text
benchmark_accuracy = (state_accuracy + signal_accuracy + transition_responsiveness) / 3
```

A component with no annotations is left out rather than counted as zero.
The components stay available and should always be shown beside the
aggregate.

## 6. Benchmark stability

Accuracy does not say whether behaviour is *consistent*:

```text
Project A:  90, 91, 89, 90, 91
Project B:  100, 50, 100, 50, 100
```

Both can share a mean; A is far more stable. Stability is the dispersion
of the observations, by standard deviation. A variable bounded to
`0..100` has a maximum standard deviation of `50`, so:

```text
stability = 100 - 2 * SD          bounded to 0..100
SD = 0  → 100
SD = 50 → 0
```

It is computed separately for state agreement, signal agreement and
transition responsiveness; the overall score is the mean of the
components that exist.

## 7. Benchmark consistency

Whether errors have a **systematic direction** — signals always
overestimated, transitions always late. A project can have good average
accuracy and poor consistency.

The mean signed error is taken per component — `actual - expected` for a
signal value, `actual position - expected position` for timing, applied
independently to message and temporal delay — and the score is the
normalized absence of that bias:

```text
100 = no systematic directional bias
0   = maximum systematic bias
```

Stability and consistency answer different questions: stability is *how
much results vary*, consistency is *whether the errors point one way*. A
project that consistently overestimates one signal scores high on the
first and low on the second.

## 8. Summary

| Metric | Measures | 100 means |
| --- | --- | --- |
| `state_accuracy` | correct expected states | every state matches |
| `signal_accuracy` | distance from expected values | every value matches |
| `transition_responsiveness` | timing of expected transitions | transitions occur where expected |
| `benchmark_accuracy` | overall agreement | perfect adherence |
| `benchmark_stability` | variability of results | completely stable |
| `benchmark_consistency` | systematic directional error | no bias |

## 9. Diagnostic statistics

Every metric retains enough to be analysed:

```text
value  sample_count  mean  median  standard_deviation  min  max
```

`sample_count` is part of the result and must never be discarded:

```text
Benchmark Accuracy: 100        Benchmark Accuracy: 100
Samples: 2                     Samples: 200
```

are not the same claim.

## 10. Interpretation rules

**Unannotated data is ignored.** A state nobody annotated does not
contribute to state accuracy; a signal nobody annotated does not
contribute to signal accuracy.

**A missing annotation is not an error.** Its absence means *not
evaluated*, never *incorrect*.

**Accuracy and stability are different.** High accuracy means the project
is close to what was expected; high stability means it behaves
consistently. Characterising behaviour needs both.

**Validity is not inferred from standard deviation.** Low variance shows
consistent behaviour relative to the annotations. It does not show the
annotations are right — that needs annotation quality, expert agreement
and a sufficient sample.

## 11. Design principle

The framework operates on **atomic observations** first and aggregates
them into metrics second. An observation carries what is needed to
compare `expected` against `actual` at one point in the conversation;
every macro-metric is an aggregation of those. That separation is what
lets further statistical analysis be added without changing the
underlying data model.

The framework reaches no ORM directly. Its DB facade must expose
`expected_state` on messages and `expected_values` on signal events.
