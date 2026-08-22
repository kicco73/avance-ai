from __future__ import annotations

from typing import Awaitable, Callable

from automaton.automaton import Automaton

# Called with the newly-active Automaton once activate_project()/put_project()
# have committed it.
CommitCallback = Callable[[str, Automaton], Awaitable[None]]
