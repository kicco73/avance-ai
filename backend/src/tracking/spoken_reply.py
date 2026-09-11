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
    and never turn it on. `wanted` is whether this particular turn has any
    use for audio at all; a contributor that ignores it would have a
    session with audio off still paying for the extra field.

    `asked` starts False and stays False when nothing contributes."""

    services: ProjectServices
    wanted: bool
    asked: bool = False

    def ask(self) -> None:
        self.asked = True
