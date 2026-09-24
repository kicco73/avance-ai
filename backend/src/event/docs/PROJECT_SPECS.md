## `event.<id>.*` — another project's live state, in a trigger

`event.<id>.state` reads a different project's current state key for the
same logged-in user — `None` with no session there — and
`event.<id>.env.<key>` that project's own action-set env value; `<key>`
must be declared in *its* `env:`. `<id>` is a literal token, never an
expression. Only reachable within the same `family` (§1): a mismatched
or missing family and an unknown id all resolve to `None` identically,
by design, and a reference that does not resolve is not a build error —
it is a runtime concern, recorded as a system warning.

```text
event.ttm_prototype.state == 'Contemplation'
event.billing.env.balance < 0
```

Only a **self-loop** action's own `trigger:` may reference `event.*` —
an action whose target is the state it is already in. A watcher is being
told something changed elsewhere, not being driven from elsewhere, so it
may act on its own state and may not be moved out of it by a project it
does not belong to. Anywhere else (`task:`, `on-exit:`, a
non-self-loop trigger) the reference is refused at build time.

The reading is not only done on the watcher's own turns: the moment the
watched project moves for that person, every project whose current state
watches it, and that the same person has a conversation in, re-evaluates
its triggers.
