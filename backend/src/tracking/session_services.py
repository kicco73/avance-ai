"""What a conversation can reach, asked of the conversation itself.

The sibling of SpokenReply, one step earlier: that one asks whether this
turn should carry a spoken reply, this one asks what the interface
showing the conversation may offer at all — whether there is any point in
a speaker button, a microphone.

It used to be answered once at boot, globally, from the *active project*
of whoever was logged in (`talk_enabled` on GET /api/core/state). Two
things were wrong with that and both were visible: a preview of another
project answered for the project the person happened to have active, and
a value that changes — the project changes, a model finishes loading —
was read once and never again.

Nobody registered for a key means nothing offers it, which is the right
answer for a build without that package.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from automaton.project_services import ProjectServices


@dataclass
class SessionServices:
    """`services` is what this session's own project declared; a
    contributor reads its own level out of it and narrows the server's
    switch with it — a project may turn a service off, never on."""

    services: ProjectServices
    available: dict[str, bool] = field(default_factory=dict)

    def offers(self, key: str, installed: bool) -> None:
        self.available[key] = self.services[key].narrow(installed)
