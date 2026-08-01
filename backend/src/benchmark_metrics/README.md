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
  max_session_duration_in_minutes: 60
```

No hardcoded 60-minute value should exist in application session management.

## Scope

`BenchmarkCalculator` supports either:

- all annotated sessions for one `username + project_name`;
- one specific session via `session_id`.

Only expert-annotated points contribute to benchmark scores.
