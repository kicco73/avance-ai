## Mail

The service name is `mail` — what `project.services:` (§1.2) declares a
level for, and what Settings > Manage services calls it.

`task.send_mail(to, body_md)` — queues an email on the job queue,
fire-and-forget, no frontend-visible effect and no return value worth
assigning. `body_md` is markdown.

A project whose action scripts call it needs the service `required`; it
is treated that way for a build whether or not the project wrote it down.
Declared `disabled`, or absent from the build, the call raises.
