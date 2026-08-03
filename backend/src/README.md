# metrics_framework

Core, domain-agnostic analytics for one user and one project.

## Architecture

`Db` -> `UserAnalyticsDataBuilder` -> `UserAnalyticsData` -> metric calculators

The framework deliberately does not import Peewee. The application's `Db`
facade remains the only database-access layer.

## Required Db facade methods

The current DB implementation needs one additional public method because the
existing API does not expose the complete Tracking event log:

```python
def get_signals(self, session_id: int) -> list[dict]:
    ...
```

It should return, chronologically or not, rows shaped as:

```text
id, timestamp, values, old_state, action, new_state
```

with `values` as the stored JSON string (or a dict). The framework separates
rows with `new_state != None` as transitions and the remaining rows as signal
snapshots.

For efficiency in production, a later DB API can expose bulk methods such as
`get_messages_for_sessions()` and `get_signals_for_sessions()`. The framework
will then need no architectural change; only the builder's data-loading
strategy changes.

## Dependencies

- pandas
- numpy

## Public usage

```python
from metrics_framework import AnalyticsCalculator

calculator = AnalyticsCalculator(db, username, project_name)
results = calculator.calculate_all()
```

Every result is normalized to `0..100`.

## Included core metrics

1. `engagement` — user message and session volume against configurable
   saturation references.
2. `retention` — fraction of consecutive session gaps that are within a
   configurable return horizon.
3. `activity_consistency` — regularity of message volume across active days.
4. `state_stability` — temporal dwell stability of the observed state timeline.
5. `signal_stability` — inverse mean absolute change of each numeric signal,
   averaged across observed signals.

These are intentionally domain-agnostic. Progress, momentum, exploration and
state-specific semantics are outside this package.

## Benchmark metrics

The `benchmark_metrics` package compares expert annotations against actual project behavior.

All benchmark metric values are normalized to `0..100`, with `100` meaning perfect.

Core benchmark metrics:

1. `state_accuracy`
2. `signal_accuracy`
3. `transition_responsiveness`
4. `benchmark_accuracy`
5. `benchmark_stability`
6. `benchmark_consistency`

The application should supply the unified system setting:

```yaml
chat-service:
  max_session_duration_in_minutes: 60
```

The benchmark framework does not access Peewee directly. Its DB facade must expose
`expected_state` on messages and `expected_values` on signal events.
