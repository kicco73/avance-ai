# What Build says on the Bus

`docs/BUS.md` is the vocabulary; this is only what this package does with
it. It publishes and subscribes to nothing — everything it does for
somebody else it does through a contribution point.

| Point | What it answers |
| --- | --- |
| `automaton.loader` | which loader answers "give me this automaton" — compiled where a package exists for the project's published revision, interpreted otherwise. Contributed only when this installation is configured to serve compiled projects |
| `project.published` | what it made of the revision somebody just published. Publishing asks nothing about whether a compiler is installed: it collects, and an installation without one collects nothing |
| `config.services` | its own section of the public services snapshot |
| `http.controllers` | its routes, under `/api/skills/build/` |
| `api.state` | that this installation can compile, for the frontend's boot state |

The choosing at `automaton.loader` belongs here because this is the only
service that knows a package can exist at all. The decision is per
project and per revision, which is why the configuration switch is a
boolean and not the name of a module.
