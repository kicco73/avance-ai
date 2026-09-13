from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from system.web_session import WebSession

if TYPE_CHECKING:
    from automaton.automaton import Automaton
    from turn.sessions.session_manager import SessionManager
    from project.project_service import ProjectService


class SessionTypeStrategy(ABC):
    type_name: str
    @abstractmethod
    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool: ...
    @abstractmethod
    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None: ...
    @abstractmethod
    def is_current(self, session: dict, active_session: dict | None) -> bool: ...
    @abstractmethod
    def caller_channel(self) -> str | None: ...
    @abstractmethod
    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool: ...
    @abstractmethod
    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str: ...
    @abstractmethod
    def revision_for(self, project_service: "ProjectService", project_id: str) -> int: ...
    @abstractmethod
    def task_for_new_session(self, automaton: "Automaton") -> dict | None: ...
    def discard_superseded(self, session_manager: "SessionManager", username: str) -> None:
        return None
    @staticmethod
    def _init_action_start(automaton: "Automaton") -> tuple[str, dict | None]:
        return automaton.init_action.target, automaton.init_action.task


class LiveSessionStrategy(SessionTypeStrategy):
    type_name = 'live'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        if session["datetime_end"] is None:
            return False
        return now - session["datetime_end"] >= open_window

    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_current(self, session: dict, active_session: dict | None) -> bool:
        return active_session is not None and active_session["id"] == session["id"]

    def caller_channel(self) -> str | None:
        return WebSession().channel

    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool:
        return self.is_current(session, active_session) and session["channel"] == channel

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        automaton, state = project_service.get_automaton_and_state(project_id, type=self.type_name, username=username)
        if automaton.new_session_strategy == "restart":
            return self._init_action_start(automaton)[0]
        return state.key

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_published_revision(project_id)

    def task_for_new_session(self, automaton: "Automaton") -> dict | None:
        if automaton.new_session_strategy == "restart":
            return self._init_action_start(automaton)[1]
        return None


class TestSessionStrategy(SessionTypeStrategy):
    type_name = 'test'
    OPEN_WINDOW = timedelta(minutes=5)

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        if session["datetime_end"] is None:
            return False
        return now - session["datetime_end"] >= self.OPEN_WINDOW

    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_current(self, session: dict, active_session: dict | None) -> bool:
        return True

    def caller_channel(self) -> str | None:
        return None

    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool:
        return True

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        revision = self.revision_for(project_service, project_id)
        automaton = project_service.get_automaton(project_id, revision)
        return self._init_action_start(automaton)[0]

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_draft_revision(project_id)

    def task_for_new_session(self, automaton: "Automaton") -> dict | None:
        return self._init_action_start(automaton)[1]


class PreviewSessionStrategy(SessionTypeStrategy):
    type_name = 'preview'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return False

    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_current(self, session: dict, active_session: dict | None) -> bool:
        return True
    def discard_superseded(self, session_manager: "SessionManager", username: str) -> None:
        session_manager.discard_sessions_of_type(username, self.type_name)

    def caller_channel(self) -> str | None:
        return None

    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool:
        return True

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        revision = self.revision_for(project_service, project_id)
        automaton = project_service.get_automaton(project_id, revision)
        return self._init_action_start(automaton)[0]

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_published_revision(project_id)

    def task_for_new_session(self, automaton: "Automaton") -> dict | None:
        return self._init_action_start(automaton)[1]


class ImportedSessionStrategy(SessionTypeStrategy):
    type_name = 'imported'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return True

    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None:
        raise NotImplementedError(
            "An imported session is never resolved-or-created — it only ever exists via import."
        )

    def is_current(self, session: dict, active_session: dict | None) -> bool:
        return False

    def caller_channel(self) -> str | None:
        return None

    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool:
        return False

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        raise NotImplementedError(
            "An imported session's state comes from the imported file, never resolved fresh."
        )

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        raise NotImplementedError(
            "An imported session's revision is stamped at import time, never resolved fresh."
        )

    def task_for_new_session(self, automaton: "Automaton") -> dict | None:
        raise NotImplementedError(
            "An imported session is never created via create_session — nothing to report."
        )


_STRATEGIES: dict[str, SessionTypeStrategy] = {
    'live': LiveSessionStrategy(),
    'test': TestSessionStrategy(),
    'preview': PreviewSessionStrategy(),
    'imported': ImportedSessionStrategy(),
}


def get_session_type_strategy(type_name: str) -> SessionTypeStrategy:
    strategy = _STRATEGIES.get(type_name)
    if strategy is None:
        raise ValueError(f"Unknown session type: {type_name!r}.")
    return strategy
