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

An evaluation point is created when the conversation is evaluated after a
user message. An expert may annotate two things there.

**Expected state** (`Message.expected_state`) — the state the system was
expected to be in after that message.

**Expected signals** (`Signals.expected_values`) — a JSON object holding
*only* the signals the expert annotated:

```json
{ "problemRecognition": 80, "planConcreteness": 60 }
```

The system's own values are in `Signals.values`. A signal absent from
`expected_values` does not participate.

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
