"""Whether this turn should also ask the model for a spoken version of
its reply.

Core builds the prompt and owns the fragment that asks (prompt.py's
AudioPrompt — the regenerate-after-transition path sends it
unconditionally, so it cannot live in a package a build may leave out).
What core does not own is the answer: a spoken reply is only worth asking
for if something can speak it, and what can speak is a skill.

It used to answer anyway, by name. `talk_configured()` read the
configuration snapshot for the key "talk", the level a project declared
was read as `services["talk"]`, and the two were combined in three
places — so core knew the name of a package it is meant to be able to
ship without, and the gate was spread across a constructor argument
threaded from main.py down through TrackingService.

Now it asks. Nobody registered means nothing speaks, which is exactly
what a build without that package should conclude.
"""
from __future__ import annotations

from dataclasses import dataclass

from automaton.project_services import ProjectServices


@dataclass
class SpokenReply:
    """`services` is what the project declared, which a contributor reads
    its own level out of — a project may narrow the server's own switch
    and never turn it on.

    Two independent answers, from two different kinds of contributor, and
    neither may be assumed to arrive first: whoever runs the interface
    this session is being had on says whether a spoken reply is any use
    here at all (`want`), and whoever can speak says whether it can be
    produced for this project (`ask`). `asked` is both, which is why it is
    computed rather than set — a contributor that read the other's answer
    while collecting would depend on registration order.

    `session_id` is which conversation is being asked about: an interface
    answers per session — the chat window's own audio toggle, a phone
    channel's reply-in-kind policy — and has nothing else to key on."""

    services: ProjectServices
    session_id: int | None = None
    wanted: bool = False
    speakable: bool = False

    @property
    def asked(self) -> bool:
        return self.wanted and self.speakable

    def want(self) -> None:
        self.wanted = True

    def ask(self) -> None:
        self.speakable = True
