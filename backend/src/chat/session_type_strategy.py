from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from automaton.automaton import Automaton

if TYPE_CHECKING:
    from project.project_service import ProjectService


class SessionTypeStrategy(ABC):
    type_name: str

    @abstractmethod
    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool: ...

    @abstractmethod
    def resolves_by_id(self) -> bool: ...

    @abstractmethod
    def starting_state(self, automaton: Automaton) -> str: ...

    @abstractmethod
    def revision_for(self, project_service: "ProjectService", project_name: str) -> int: ...


class LiveSessionStrategy(SessionTypeStrategy):
    type_name = 'live'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        if session["datetime_end"] is None:
            return False
        return now - session["datetime_end"] >= open_window

    def resolves_by_id(self) -> bool:
        return False

    def starting_state(self, automaton: Automaton) -> str:
        raise NotImplementedError(
            "Live sessions never start from a fixed state — the caller resolves the current one explicitly."
        )

    def revision_for(self, project_service: "ProjectService", project_name: str) -> int:
        published_revision = project_service._db.get_project_published_revision(project_name)
        if published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published.")
        return published_revision


class TestSessionStrategy(SessionTypeStrategy):
    type_name = 'test'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return False

    def resolves_by_id(self) -> bool:
        return True

    def starting_state(self, automaton: Automaton) -> str:
        return automaton.init_action.target

    def revision_for(self, project_service: "ProjectService", project_name: str) -> int:
        return project_service._db.get_project_revision(project_name)


class ImportedSessionStrategy(SessionTypeStrategy):
    type_name = 'imported'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return False

    def resolves_by_id(self) -> bool:
        return True

    def starting_state(self, automaton: Automaton) -> str:
        raise NotImplementedError(
            "An imported session's state comes from the imported file, never resolved fresh."
        )

    def revision_for(self, project_service: "ProjectService", project_name: str) -> int:
        raise NotImplementedError(
            "An imported session's revision is stamped at import time, never resolved fresh."
        )


_STRATEGIES: dict[str, SessionTypeStrategy] = {
    'live': LiveSessionStrategy(),
    'test': TestSessionStrategy(),
    'imported': ImportedSessionStrategy(),
}


def get_session_type_strategy(type_name: str) -> SessionTypeStrategy:
    strategy = _STRATEGIES.get(type_name)
    if strategy is None:
        raise ValueError(f"Unknown session type: {type_name!r}.")
    return strategy
