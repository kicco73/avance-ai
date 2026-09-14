# What Event says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

**Contributes** to `trigger.namespaces` — the `event` root a trigger may
reference. That is where a build learns that `event.<id>.state` is a name
and not a typo, where a running scope finds what it resolves to, and where
the editor asks what to list under it. The contribution is registered at
start, ahead of the boot-time availability sweep, so a project that was
already watching another one builds the same at boot as it does later.

**Subscribes** to `state.changed` and `env.changed` — what a conversation
did, addressed to the user it happened to. Both carry the project on the
envelope; for every project that user has a conversation in, the current
state's own self-loop triggers say whether it watches that one.

**Publishes** `ui.notification` — `{project_name, state, buttons}` — when
a watching project moved without anybody speaking to it. That is the whole
outbound surface: the transition is already recorded by the time the
message goes out, so nobody carrying it costs the watcher nothing.

Composition is deliberately late. The service re-evaluates against the
tracking engine and the task namespaces, which exist only once the core
is composed, so it is built from `bus.POINT_CORE_SERVICES` at
`http.controllers` time rather than at boot.
