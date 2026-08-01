# metrics_framework

Core, domain-agnostic analytics for one user and one project.

## Architecture

`Db` -> `UserAnalyticsDataBuilder` -> `UserAnalyticsData` -> metric calculators

The framework deliberately does not import Peewee. The application's `Db`
facade remains the only database-access layer.

## Required Db facade methods

The current DB implementation needs one additional public method because the
existing API does not expose the complete Signals event log:

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

# Analytics Metrics Framework

## 1. Purpose

The Analytics Metrics Framework provides **domain-independent metrics** for analyzing a user's longitudinal interaction with a conversational application.

It is designed to work across different domains, such as:

* therapy and behavioral support;
* language learning;
* coaching;
* training.

The framework measures **observable behavior and temporal patterns**.

It does not define domain-specific concepts such as:

* success;
* failure;
* therapeutic progress;
* learning proficiency;
* achievement.

Those concepts are defined by the project using the framework.

---

# 2. Core Metrics

All core metrics return a normalized value in the range **0–100**.

The value is intended for visualization and comparison:

```text
0     minimum
50    intermediate
100   maximum
```

A high value does not necessarily mean "better". Its interpretation depends on the metric.

---

## 2.1 Engagement

**Measures:** how much the user interacts with the system.

Based primarily on:

* number of user messages;
* number of sessions;
* optionally active periods/days.

Conceptually:

```text
Engagement
    ├── message activity
    └── session activity
```

High engagement means that the user interacts frequently or substantially with the application.

It does **not** measure:

* quality of interaction;
* progress;
* achievement;
* state quality.

---

## 2.2 Retention

**Measures:** how consistently the user returns over time.

Unlike simple session frequency, retention is explicitly temporal.

Relevant observations include:

* session timestamps;
* intervals between sessions;
* inactivity gaps;
* repeated return behavior.

Example:

```text
10 sessions in 10 days
```

and:

```text
10 sessions across 12 months
```

have the same session count but very different retention characteristics.

---

## 2.3 Activity Consistency

**Measures:** how evenly the user's activity is distributed over time.

It is distinct from Engagement.

Example:

```text
5  5  5  5  5  5  5
```

is highly consistent, while:

```text
0  0  0  35  0  0  0
```

is highly concentrated.

The metric considers the temporal distribution and variability of user activity.

---

## 2.4 State Stability

**Measures:** how stable the user's state remains over time.

Based on:

* state transitions;
* transition frequency;
* time between transitions;
* temporal transition density.

The framework treats state names as **opaque identifiers**.

For example:

```text
precontemplation
action
maintenance
crisis
```

have no inherent ordering or value for the core framework.

Therefore:

```text
A → B → C
```

is interpreted as state movement, not automatically as progress.

A high State Stability score means that the user's state classification changes relatively little over time.

It does **not** mean that the current state is desirable.

---

## 2.5 Signal Stability

**Measures:** how stable a numerical signal is over time.

Example:

```text
40 → 43 → 41 → 44
```

is relatively stable.

```text
10 → 90 → 20 → 85
```

is highly variable.

The metric uses temporal dispersion/variability of signal observations.

The core framework does not interpret the semantic direction of a signal:

```text
80 > 40
```

does not inherently mean that 80 is better than 40.

That interpretation belongs to the domain.

---

# 3. Core vs. Domain Metrics

The framework separates **measurement** from **domain interpretation**.

### Core metrics

Always available:

```text
Engagement
Retention
Activity Consistency
State Stability
Signal Stability
```

### Domain metrics

Defined by the project:

```text
Progress
Momentum
Exploration
Achievement
Proficiency
Therapeutic outcome
```

---

# 4. Progress

Progress is intentionally **not a core metric**.

Its definition depends on the project.

For example, a project may define progress from states:

```text
state → progress contribution
```

Another may define it from signals:

```text
signal A
signal B
signal C
    ↓
progress
```

Or combine several dimensions:

```text
state
+
signals
+
conditions
    ↓
progress
```

The framework therefore provides the data and statistical primitives required to implement Progress, but does not impose its meaning.

---

# 5. Momentum

Momentum is derived from a project's definition of Progress.

Conceptually:

```text
Progress(t)
    ↓
temporal change / trend
    ↓
Momentum
```

Momentum therefore belongs to the configurable/domain layer.

The core framework provides the historical and temporal data required to calculate it.

---

# 6. Exploration

Exploration is also domain-dependent.

A project may define it using, for example:

* variety of states;
* variety of signals;
* breadth of interaction;
* coverage of project-defined areas.

The core framework does not prescribe a universal definition.

---

# 7. Fundamental Principle

The framework separates:

```text
OBSERVATION
    ↓
INTERPRETATION
```

The core observes:

* messages;
* sessions;
* timestamps;
* signals;
* state transitions;
* temporal patterns.

The project interprets them.

For example:

```text
Core:
State remained stable for 23 days.

Domain:
This represents successful maintenance.
```

or:

```text
Core:
Exercise performance increased from 61 to 78.

Domain:
The user's proficiency is improving.
```

The framework provides the first layer.

---

# 8. Data Scope

All analytics are scoped to:

```text
username + project_name
```

Data from different projects is never mixed.

The database is the authoritative source for:

* messages;
* sessions;
* signals;
* state transitions.

Session boundaries are also authoritative. The analytics framework does not reconstruct sessions from message timestamps.

---

# 9. Analytical Timeline

Metrics operate on longitudinal data rather than isolated database records.

The framework builds an analytical representation containing:

```text
UserAnalyticsData
    ├── messages
    ├── sessions
    ├── signals
    └── transitions
```

The timeline makes temporal operations explicit, including:

* intervals;
* durations;
* frequencies;
* variability;
* trends;
* transition density;
* rolling statistics.

It is a derived analytical representation, not a second persistence layer.

---

# 10. Architecture

The dependency flow is:

```text
Database
    ↓
Data Builder
    ↓
UserAnalyticsData
    ↓
Metric Calculators
    ↓
MetricResult
```

Only the data-building layer knows about the database implementation.

Metrics never query the database directly.

Therefore:

```text
Metric
   ✗ → Peewee
   ✗ → SQLite
   ✗ → database models
```

Instead:

```text
Metric
   ✓ → UserAnalyticsData
```

This keeps metric implementations independent of the persistence layer.

---

# 11. Analytical Data Representation

Pandas is used for analytical data and time-series operations.

Typical structures include:

```text
DataFrame
    messages
    sessions
    transitions

DataFrame / Series
    individual signal histories
```

Pandas provides the required operations for:

* grouping;
* aggregation;
* time differences;
* rolling windows;
* variance;
* standard deviation;
* temporal indexing;
* resampling.

NumPy may be used for numerical operations where appropriate.

---

# 12. Metric Interface

Every metric exposes the same conceptual interface:

```python
calculate(data: UserAnalyticsData) -> MetricResult
```

The consumer therefore does not need to know how a metric is implemented.

Conceptually:

```python
result = metric.calculate(data)
```

Metrics are independent classes and should not rely on global state.

---

# 13. Metric Result

Each calculation returns a `MetricResult`.

Conceptually:

```python
MetricResult(
    name="signal_stability",
    value=78.4,
    components={...},
    calculated_at=...,
    metadata={...},
)
```

### Main fields

| Field           | Purpose                                |
| --------------- | -------------------------------------- |
| `name`          | Stable metric identifier               |
| `value`         | Main normalized value, `0..100`        |
| `components`    | Optional contributing values           |
| `calculated_at` | Calculation timestamp                  |
| `metadata`      | Optional diagnostic/configuration data |

`components` allow dashboards and diagnostics to expose the internal dimensions of a metric without coupling the consumer to its implementation.

---

# 14. Normalization

Every core metric produces a value between:

```text
0..100
```

Normalization is a common presentation convention.

It does not imply that:

* all metrics have the same semantics;
* 80 in one metric is equivalent to 80 in another;
* high always means desirable.

The normalization layer is responsible only for mapping metric-specific measurements to the common scale.

---

# 15. Database Boundary

The database layer remains responsible for persistence.

The analytics framework consumes the existing `Db` abstraction and does not introduce a second database access mechanism.

Conceptually:

```text
Db
 ↓
Timeline/Data Builder
 ↓
UserAnalyticsData
```

The framework does not import Peewee models into metric implementations.

---

# 16. No Cache or Snapshot Requirement

The initial implementation calculates metrics on demand.

It does not require:

* cached values;
* persisted metric snapshots;
* metric history tables.

This keeps the first implementation simple and avoids cache invalidation and synchronization concerns.

Caching or snapshots can be added later without changing the metric interface.

---

# 17. Extending the Framework

A new metric should:

1. implement the standard metric interface;
2. consume `UserAnalyticsData`;
3. return `MetricResult`;
4. normalize its main value to `0..100`;
5. avoid database-specific logic;
6. avoid assumptions about domain semantics.

Conceptually:

```python
class NewMetric(object):

    name = "new_metric"

    def calculate(
        self,
        data: UserAnalyticsData,
    ) -> MetricResult:
        ...
```

The consumer can then use it exactly like any existing metric.

---

# 18. Testing

Metric tests should operate independently of the database.

Typical test datasets should cover:

* empty history;
* single session;
* high activity;
* low activity;
* consistent activity;
* bursty activity;
* stable signals;
* volatile signals;
* no state transitions;
* frequent transitions;
* long stable states;
* repeated short states.

Database integration tests should separately verify the transformation from persisted records into `UserAnalyticsData`.

---

# 19. Future Extensions

The architecture supports future additions such as:

* rolling metrics;
* trend detection;
* anomaly detection;
* signal correlation;
* configurable Progress;
* Progress Momentum;
* Exploration;
* domain-specific composite metrics;
* metric snapshots;
* cached calculations;
* longitudinal dashboards;
* ML-derived metrics.

These should be implemented as additional layers rather than embedded in the core metrics.

---

# 20. Summary

The framework provides a stable, domain-independent analytical foundation.

```text
Messages
Sessions
Signals
States
    ↓
Temporal analytical data
    ↓
Core metrics
    ↓
0..100 normalized measurements
    ↓
Domain-specific interpretation
    ↓
Progress / Momentum / Exploration / Outcome
```

The core framework answers:

> **What can be observed about the user's interaction and its evolution over time?**

The project layer answers:

> **What does that evolution mean for this particular application?**
