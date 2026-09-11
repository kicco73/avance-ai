# The skills

Avance is sold and delivered one skill at a time. A skill is a whole
capability — a channel to talk over, a voice, a way to measure, a
compiler — and an installation contains exactly the ones it was built
with.

What follows is this installation: every section below is written by the
skill it describes, so this page lists what is actually here and nothing
else. How a skill is built, isolated and left out of a build is a
separate matter — see `docs/BUILDING_PLATFORMS.md`.

Two words recur. A skill is **installed** when its package is part of
this build — a decision taken once, when the build was produced, that
nothing at run time can change. A skill is **configured** when the
installation has filled in what it needs to work (an account, a key, a
model). Installed but unconfigured is a normal state: the skill says so
in Settings › Manage services rather than pretending to work.

Some skills can also be declared **per project**: a project states in its
own `index.yml` whether a service is `required`, `optional` or
`disabled`, and a project that disabled one never reaches it even where
the installation offers it.
