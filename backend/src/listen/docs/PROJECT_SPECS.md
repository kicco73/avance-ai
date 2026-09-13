## Listen

The service name is `listen` — what `project.services:` (§1.2) declares a
level for, and what Settings > Manage services calls it.

It adds no `task.*` call and no `index.yml` field. What a project asks
for by declaring it `required` is that a spoken message be understood:
what the person said enters the conversation as their own turn, exactly
as if they had written it, so nothing in `index.yml` distinguishes the
two. Declared `disabled`, or absent from the build, there is no
microphone.
