# What Mail says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it.

**Subscribes** to `tool.send_mail` — `{to, subject, body_md}` — and
queues the message on the job queue. That is the whole inbound surface: a
project that wants mail sent publishes the type and never imports this
package, so a build without it simply has nobody registered, which is a
normal outcome rather than an error.

Composition is deliberately late. Anything that publishes `tool.send_mail`
does so once the core is composed, so the service is built on first use
from `bus.POINT_CORE_SERVICES` rather than taking the core as a
constructor parameter.

**Contributes** its own section of the public services snapshot
(`config.services`).
