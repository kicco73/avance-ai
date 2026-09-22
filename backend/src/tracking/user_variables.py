from __future__ import annotations

from dataclasses import dataclass

from automaton.automaton import Automaton, State
from turn.turn_transaction import RowHandle


@dataclass(frozen=True, slots=True)
class UserVariables:
	automaton: Automaton
	state: State
	project_id: str
	session_id: int
	message_id: RowHandle | None = None
	has_ai_started_conversation: bool = False
