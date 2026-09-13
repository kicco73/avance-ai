## WhatsApp

The service name is `whatsapp` — what `project.services:` (§1.2) declares
a level for, and what Settings > Manage services calls it.

`task.whatsapp(phone_number, message_md)` — sends a WhatsApp message to
`phone_number` (E.164 digits, `+` optional) through the same Cloud API
the WhatsApp channel sends its replies with, markdown converted the same
way.

Unlike a fire-and-forget task call, the task blocks on the API call and
the statement's own value is real: `True` once the message is accepted,
`False` — nothing sent — for a `phone_number` with no linked user account
or a failed API call. A script can react to it:

```yaml
task: |
  sent = task.whatsapp(to, body)
```

Once sent, `message_md` is also appended as an `assistant` message to the
recipient's own live session on *this* action's project (the one bound to
the task namespace the script is running under, not necessarily the
recipient's own active project) — their currently open session if there
is one, on any channel, or a freshly opened `whatsapp-chat` one
otherwise. Best-effort: a failure recording it never turns a successful
send back into `False`.

A project whose action scripts call it needs the service `required`; it
is treated that way for a build whether or not the project wrote it down.
Declared `disabled`, or absent from the build, the call returns `False`
without sending anything.
