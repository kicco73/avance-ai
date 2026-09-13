# Core metrics

Core, domain-agnostic analytics for one user and one project.

## Architecture

`Db` -> `UserAnalyticsDataBuilder` -> `UserAnalyticsData` -> metric calculators

The framework deliberately does not import Peewee. The application's `Db`
facade remains the only database-access layer.

## Required Db facade methods

The framework relies on one Db method for the complete Tracking event log:

```python
def get_signals(self, session_id: int) -> list[dict]:
    ...
```

It returns, chronologically or not, rows shaped as:

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
