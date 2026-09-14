## What `automaton.<id>.*` is worth when nobody is speaking

`automaton.<id>.state` and `automaton.<id>.env.<key>` (§5.2) read another
project's live state for the same person. With this skill installed the
reading is not only done on the watcher's own turns: the moment the
watched project moves for that person, every project that watches it and
that the same person has a conversation in re-evaluates its triggers.

Only a **self-loop** fires that way — an action whose target is the state
it is already in. A watcher is being told something changed elsewhere,
not being driven from elsewhere, so it may act on its own state and may
not be moved out of it by a project it does not belong to.
