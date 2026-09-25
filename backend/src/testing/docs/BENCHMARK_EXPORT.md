# Benchmark export

**Export**, at the bottom of the test explorer, downloads everything the
explorer holds for the selected strategy as one JSON file. It is enabled
once at least one test has completed.

The metrics themselves are defined in the Benchmark document.

## Top level

```json
{
  "metrics": { "<metric>": { ... } },
  "run":     { ... }
}
```

## `metrics`

Every benchmark metric, keyed by its name. Result objects under `run` use
the same keys.

```json
"state_accuracy": {
  "label": "State Accuracy",
  "description": "Percentage of expert-annotated points where ...",
  "scope": ["all_sessions", "all_sessions_per_user", "one_session"]
}
```

`scope` lists where the metric applies: a single session only reports the
metrics whose scope includes `one_session`.

## `run`

The explorer tree, one branch per root node.

```json
"run": {
  "project_id": "hello_world",
  "strategy": "batch_lite",
  "exported_at": "2026-09-25T10:12:00+00:00",
  "sessions": {
    "results": { "<metric>": <statistics> },
    "sessions": { "<session id>": <session> }
  },
  "states": {
    "results": { "signal_accuracy": <statistics> },
    "states": { "<state key>": { "results": { "signal_accuracy": <statistics> } } }
  },
  "users": {
    "results": { "<metric>": <statistics> },
    "users": {
      "<username>": {
        "results": { "<metric>": <statistics> },
        "sessions": ["<session id>", ...]
      }
    }
  },
  "signals": {
    "results": { "signal_accuracy": <statistics> },
    "signals": { "<signal>": { "label": "...", "results": { "signal_accuracy": <statistics> } } }
  }
}
```

- `strategy` is the one selected in the panel: `batch_lite`, `batch` or
  `turn_by_turn`.
- `results` is `null` for a node that has not been tested for the current
  version of the project.
- The sessions under a user are the ids of entries in
  `run.sessions.sessions`, not copies.
- States and signals measure signal accuracy only, so their results carry
  the single key `signal_accuracy`. On the `states` and `signals` branches
  it is the explorer's **Overall** row: the mean of the children weighted
  by their samples.

### A session

```json
"12": {
  "title": "First contact",
  "username": "alice",
  "datetime_start": "2026-09-20T09:00:00",
  "datetime_end": "2026-09-20T09:14:00",
  "start_state": "Hello",
  "end_state": "Goodbye",
  "turns": 7,
  "stale": false,
  "results": { "<metric>": <statistics> }
}
```

- `start_state` and `end_state` are those of the recorded conversation.
- `turns` counts the user messages.
- `stale` is `true` when the project has changed since the test ran;
  `null` when it has not run.
- `results` is the latest completed test under the strategy.

### Statistics

```json
{
  "value": 83.3,
  "mean": 83.3,
  "median": 100.0,
  "standard_deviation": 23.5,
  "minimum": 50.0,
  "maximum": 100.0,
  "sample_count": 6,
  "distribution": [0, 0, 0, 0, 0, 1, 0, 0, 0, 5],
  "components": { "problemRecognition": 91.0 }
}
```

- Every value is on the `0..100` scale, 100 being perfect.
- `value` is the score the explorer shows; `mean`, `median`,
  `standard_deviation`, `minimum` and `maximum` are `null` when there is
  nothing to compute them from, and on the weighted Overall rows.
- `sample_count` is the number of annotated observations behind the score.
- `distribution` is the histogram of the samples: ten buckets of ten
  points each, `0..10` first, with `100` counted in the last.
- `components` breaks the score down where the metric has parts, such as
  signal accuracy per signal; it is empty otherwise.
