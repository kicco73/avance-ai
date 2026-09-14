# What Event says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

**Subscribes** to `state.changed` and `env.changed` — what a conversation
did, addressed to the user it happened to. Both carry the project on the
envelope, which is what lets this package ask which other projects were
watching that one.

**Publishes** `ui.notification` — `{project_name, state, buttons}` — when
a watching project moved without anybody speaking to it. That is the whole
outbound surface: the transition is already recorded by the time the
message goes out, so nobody carrying it costs the watcher nothing.

Composition is deliberately late. The service re-evaluates against the
tracking engine and the task namespaces, which exist only once the core
is composed, so it is built from `bus.POINT_CORE_SERVICES` at
`http.controllers` time rather than at boot.
