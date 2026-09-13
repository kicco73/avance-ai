## Talk

The service name is `talk` — what `project.services:` (§1.2) declares a
level for, and what Settings > Manage services calls it.

It adds no `task.*` call and no `index.yml` field. What a project asks
for by declaring it `required` is that every reply be synthesized to
audio and offered for playback in the conversation. Declared `disabled`,
or absent from the build, no `audio` metadata is ever asked for and
conversations stay in text.

> **Deprecated:** `project.talk-enabled: true|false` still reads as
> `services: {talk: required}` / `services: {talk: disabled}` and builds
> with a warning. Write `services:` instead.
