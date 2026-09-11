# The skills

Avance is sold and delivered one skill at a time. A skill is a whole
capability — a channel to talk over, a voice, a way to measure, a
compiler — and an installation contains exactly the ones it was built
with. This page says what each one does for the people using it. How a
skill is built, isolated and left out of a build is a separate matter:
see `docs/BUILDING_PLATFORMS.md`.

Two words recur below. A skill is **installed** when its package is part
of this build — a decision taken once, when the build was produced, that
nothing at run time can change. A skill is **configured** when the
installation has filled in what it needs to work (an account, a key, a
model). Installed but unconfigured is a normal state: the skill says so
in Settings › Manage services rather than pretending to work.

Some skills can also be declared **per project**: a project states in its
own `index.yml` whether a service is `required`, `optional` or
`disabled`, and a project that disabled one never reaches it even where
the installation offers it.

| skill | what it adds | per project | needs configuring |
|---|---|---|---|
| Platform | authoring, administration, benchmarking screens | – | – |
| Native chat | conversations in the browser | – | – |
| WhatsApp | conversations over WhatsApp | yes | yes |
| Talk | spoken replies | yes | yes |
| Listen | voice messages understood as text | yes | yes |
| Mail | email sent on a project's behalf | yes | yes |
| Testing | benchmark runs and their aggregated results | – | – |
| Build | compiling a project into a deliverable package | – | – |
| Product | serving one compiled project | – | – |

## Platform

Everything a person does to a project *other than have a conversation
with it*: the project editor, the graph, sources and files, revisions and
publishing, the app store, user management, invitations, Settings and
Manage services, login and the profile page.

This is the authoring side of the product. An installation without it
still holds conversations on whatever channels it was built with — it
simply offers no way to change, inspect or administer anything, and
nothing on screen suggests an editor ever existed.

## Native chat

Conversations in the browser: the chat window, its live turn-by-turn
streaming, and the handover to a human operator when a conversation asks
for one.

Without it the system runs, but nobody can hold a conversation from a
browser — the socket is open and no one answers a turn on it.

## WhatsApp

Conversations over WhatsApp, through the WhatsApp Cloud API: people reach
a project from their own phone, and the same conversation, memory and
rules apply as in any other channel. Invitation links can point at
WhatsApp, and a person's phone number becomes part of their profile.

Needs an account with Meta and its credentials filled in. A project that
never sends a WhatsApp task does not need this skill, and the Build view
works that out on its own.

## Talk

Spoken replies: a project's answers are synthesized to audio, and each
message can be played back in the conversation.

Needs a voice model configured. Where it is absent, conversations stay
in text and no audio control appears at all.

## Listen

Voice messages understood as text: a person speaks, and what they said
enters the conversation as their turn.

Needs a speech model configured; the model loads in the background after
start, so the capability reports itself as unavailable for the first
moments and available afterwards. Where it is absent, there is no
microphone.

## Mail

Email sent on a project's behalf, when a conversation reaches a point
that calls for one.

Needs a mail account configured. A project that never sends mail does not
need this skill, and the Build view works that out on its own.

## Testing

Measuring, rather than running, conversations. Replays recorded sessions
against the current project, scores each run against what was expected,
and aggregates the results per state, per signal, per user and per
project, with the trends behind them.

Benchmarking is on wherever the skill is installed; there is nothing to
configure beyond how many runs may share the pool. Without it, an
installation runs conversations perfectly well and cannot measure them.

## Build

Compiles a published project revision into a standalone package, and
produces the deliverable backend that carries a chosen set of skills —
the mechanism by which everything on this page is sold separately.

An installation without it still authors and publishes projects: it
simply has nothing to compile them into. An editor that cannot compile is
a perfectly good editor.

## Product

The other end of Build: an installation that *is* one compiled project.
It reads its project from the package delivered beside it rather than
from the authoring database, and answers on the channels it was built
with.

It is not something an operator turns on. It describes what a delivered
installation is, and it stands aside automatically in any build that can
also compile — that installation decides per project and per revision
which one answers.
