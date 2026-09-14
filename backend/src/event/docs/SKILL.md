## Event

A project that watches another one — `automaton.<id>.state`,
`automaton.<id>.env.<key>` in a trigger — is told when what it watches
moves, instead of finding out the next time somebody speaks to it. The
watching project re-evaluates its own triggers there and then, and the
person sees the result arrive on its own.

Needs nothing configured. A project whose triggers read no other project
does not need this skill, and a build works that out for itself.
