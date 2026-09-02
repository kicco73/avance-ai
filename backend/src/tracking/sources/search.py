from __future__ import annotations

from automaton.automaton import Automaton
from db import Db

from . import attachment as attachment_source


def read(db: Db, automaton: Automaton, what: str, where: str) -> str:
    content = attachment_source.read(db, automaton, where)
    lines = content.splitlines(keepends=True)
    if not lines:
        return ""
    needle = what.lower()
    matches = [line for line in lines[1:] if needle in line.lower()]
    return lines[0] + "".join(matches)
