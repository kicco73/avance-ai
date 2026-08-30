# Benchmark Metrics

Domain-independent benchmark metrics comparing expert annotations with actual project behavior.

All metric values use `0..100`, where **100 means perfect behavior**.

## Core metrics

- `state_accuracy`: percentage of annotated state points where actual and expected states match.
- `signal_accuracy`: mean agreement over only annotated signal values. `100 - abs(actual - expected)`.
- `transition_responsiveness`: responsiveness to expected state changes, combining normalized message delay and time delay.
- `benchmark_accuracy`: mean of available state accuracy, signal accuracy and transition responsiveness components.
- `benchmark_stability`: consistency of normalized errors; lower dispersion means higher stability.
- `benchmark_consistency`: absence of systematic directional error in signal values and transition timing; no bias is `100`.

## Timing

`BenchmarkConfiguration.max_session_duration_in_minutes` is the single temporal reference. The application should supply it from:

```yaml
chat-service:
  max-session-duration-in-minutes: 60
```

No hardcoded 60-minute value should exist in application session management.

## Scope

`BenchmarkCalculator` supports either:

- all annotated sessions for one `username + project_name`;
- one specific session via `session_id`.

Only expert-annotated points contribute to benchmark scores.

# Benchmark Metrics

The Benchmark Metrics framework evaluates how closely a chat-based project behaves according to **expert-domain expectations**.

The expert annotations stored in the database represent the benchmark ground truth. Metrics are calculated only from explicitly annotated evaluation points.

The framework supports two scopes:

* **Session scope** — metrics for one annotated chat session.
* **Project scope** — metrics aggregated across all annotated sessions of a project.

No metric considers unannotated expectations as errors.

---

## 1. Benchmark observations

An evaluation point is created when the system evaluates the conversation after a user message.

An expert may annotate:

### Expected state

```text
Message.expected_state
```

The state the system is expected to have after that message.

### Expected signals

```text
Signals.expected_values
```

A JSON object containing only the signals explicitly annotated by the expert:

```json
{
  "problemRecognition": 80,
  "planConcreteness": 60
}
```

The system's corresponding values are stored in:

```text
Signals.values
```

Only signals present in `expected_values` participate in the benchmark.

---

# 2. Normalization

All benchmark scores use the same convention:

```text
100 = perfect
0   = worst
```

All public metric values are normalized to `0..100`.

Raw distances and errors may be used internally, but are never exposed as the primary metric value.

---

# 3. State Accuracy

Measures whether the system reaches the state expected by the expert at an annotated point.

For each annotated state:

```text
actual_state == expected_state
```

produces:

```text
100 = match
0   = mismatch
```

The aggregate is:

[
StateAccuracy =
\frac{\text{correct state annotations}}
{\text{state annotations}}
\times 100
]

Example:

```text
10 annotated states
8 correct

State Accuracy = 80
```

Only messages with `expected_state` are included.

---

# 4. Signal Accuracy

Measures how close the system's signal values are to the expert's expected values.

For each annotated signal:

[
error = |actual - expected|
]

Since signals range from `0..100`, the corresponding accuracy is:

[
SignalAccuracy = 100 - |actual - expected|
]

Examples:

| Expected | Actual | Accuracy |
| -------: | -----: | -------: |
|       80 |     80 |      100 |
|       80 |     70 |       90 |
|       80 |     50 |       70 |
|       80 |      0 |       20 |

The aggregate Signal Accuracy is the arithmetic mean of the accuracies of **only the annotated signals**.

The framework also provides per-signal results, for example:

```text
problemRecognition    91
decisionalBalance     84
planConcreteness      63
```

This prevents a good overall score from hiding a consistently poor individual signal.

---

# 5. Transition Responsiveness

State correctness alone does not describe **when** the expected state is reached.

An expert annotation may identify a point where a state transition is expected.

The framework measures the distance between:

```text
expected transition point
actual transition point
```

Two independent distances are considered.

## Message delay

The difference between the expected and actual message positions.

```text
0 = transition at the expected point
```

A transition occurring earlier or later produces an error.

The absolute delay is normalized against the maximum relevant message distance in the session.

## Time delay

The difference between expected and actual transition timestamps.

The maximum possible delay is bounded by:

```text
max_session_duration_in_minutes
```

configured globally for the chat service.

For example, with a 60-minute maximum session duration:

```text
0 minutes delay  → 100
30 minutes delay → 50
60 minutes delay → 0
```

The time component therefore remains meaningful because the session itself cannot remain open indefinitely.

## Combined score

Message responsiveness and temporal responsiveness are independently normalized to `0..100`, then combined with equal weight:

[
TransitionResponsiveness =
\frac{MessageResponsiveness + TimeResponsiveness}{2}
]

Thus:

```text
100 = transition exactly where expected
0   = maximum observable delay
```

The framework retains both components so that a project can be diagnosed separately for conversational and temporal responsiveness.

---

# 6. Benchmark Accuracy

A single high-level measure of how closely the project follows expert expectations.

It combines:

* State Accuracy
* Signal Accuracy
* Transition Responsiveness

with equal weights:

[
BenchmarkAccuracy =
\frac{
StateAccuracy +
SignalAccuracy +
TransitionResponsiveness
}{3}
]

Therefore:

```text
100 = perfect agreement with the benchmark
0   = worst possible agreement
```

The component scores remain available and should always be displayed alongside the aggregate score.

---

# 7. Benchmark Stability

Accuracy does not indicate whether the project behaves consistently.

Example:

```text
Project A:
90, 91, 89, 90, 91

Project B:
100, 50, 100, 50, 100
```

Both can have a similar mean, but Project A is considerably more stable.

Benchmark Stability measures the dispersion of benchmark observations using **standard deviation**.

For each component, the standard deviation is calculated over its normalized `0..100` observations.

Because a variable bounded between `0..100` has a maximum possible standard deviation of `50`, dispersion can be converted to a stability score:

[
Stability = 100 - 2 \times SD
]

with the result bounded to `0..100`.

Therefore:

```text
SD = 0   → Stability = 100
SD = 50  → Stability = 0
```

The framework can calculate stability separately for:

* state agreement;
* signal agreement;
* transition responsiveness.

The overall Benchmark Stability is the arithmetic mean of the available component stability scores.

A high score means that the project behaves consistently relative to the benchmark.

A low score indicates potentially unstable or highly variable behavior.

---

# 8. Benchmark Consistency

This metric replaces the ambiguous concept of "Benchmark Bias".

The objective is to measure whether the system has a **systematic tendency to deviate in one direction**.

Examples:

* signals consistently overestimated;
* signals consistently underestimated;
* transitions consistently too early;
* transitions consistently too late.

A system can therefore have good average accuracy but poor consistency.

For each signed error, the framework calculates the mean directional deviation.

The resulting score is:

```text
100 = no systematic directional bias
0   = maximum systematic bias
```

For signal values, the directional error is:

[
actual - expected
]

For transition timing:

[
actual\ transition\ position - expected\ transition\ position
]

and the same concept is applied independently to message and temporal delay.

The final Benchmark Consistency score is derived from the normalized absence of directional bias across the available components.

Unlike Stability:

```text
Stability
    → how much results vary

Consistency
    → whether errors systematically point in one direction
```

A project may therefore be:

```text
high stability + low consistency
```

if it behaves very consistently but consistently overestimates a signal.

---

# 9. Core metrics summary

| Metric                        | Measures                           | 100 means                               |
| ----------------------------- | ---------------------------------- | --------------------------------------- |
| **State Accuracy**            | Correct expected states            | All states match                        |
| **Signal Accuracy**           | Signal distance from expert values | All values match                        |
| **Transition Responsiveness** | Timing of expected transitions     | Transitions occur at the expected point |
| **Benchmark Accuracy**        | Overall agreement                  | Perfect benchmark adherence             |
| **Benchmark Stability**       | Variability of results             | Completely stable behavior              |
| **Benchmark Consistency**     | Systematic directional error       | No systematic bias                      |

---

# 10. Diagnostic statistics

Every metric should retain sufficient information for analysis, including:

```text
value
sample_count
mean
median
standard_deviation
min
max
```

All exposed score-like values follow the `0..100` convention.

Diagnostic statistics must always be interpreted together with `sample_count`.

For example:

```text
Benchmark Accuracy: 100
Samples: 2
```

is not equivalent to:

```text
Benchmark Accuracy: 100
Samples: 200
```

The number of observations is therefore part of the benchmark result and must not be discarded.

---

# 11. Session vs. project scope

The same metric definitions are used at both scopes.

### Session

Calculates metrics from the annotations belonging to one session.

### Project

Aggregates observations from all annotated sessions belonging to the project.

The metric implementation must not change depending on the scope. Scope only determines which observations are supplied to the calculation.

---

# 12. Important interpretation rules

### Unannotated data is ignored

If an expert did not annotate a state, it does not contribute to State Accuracy.

If an expert did not annotate a signal, it does not contribute to Signal Accuracy.

### Missing annotations are not errors

Absence of an annotation means:

```text
not evaluated
```

not:

```text
incorrect
```

### Accuracy and stability are different

A high Accuracy score means the project is close to the expected behavior.

A high Stability score means its behavior is consistent.

Both are required to properly characterize project behavior.

### Benchmark validity is not inferred from standard deviation

Low variance indicates consistent project behavior relative to the annotations.

It does **not** by itself prove that the expert benchmark is valid.

Benchmark validity requires independent considerations such as annotation quality, expert agreement, and sufficient sample size.

---

# 13. Design principle

The framework should conceptually operate on **atomic benchmark observations** first and aggregate them into metrics second.

An observation contains the information necessary to compare:

```text
expected
vs.
actual
```

at a specific point in the conversation.

Macro-metrics are aggregations of these observations.

This separation allows additional statistical analyses to be introduced later without changing the underlying benchmark data model.
