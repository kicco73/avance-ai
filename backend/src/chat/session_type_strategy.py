from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from session import Session

if TYPE_CHECKING:
    from automaton.automaton import Automaton
    from chat.session_manager import ChatSessionManager
    from project.project_service import ProjectService


class SessionTypeStrategy(ABC):
    type_name: str

    # Whether a session of this type counts as expired given how long it's
    # been since its last activity (datetime_end) — governs whether it's
    # still usable without starting a new one.
    @abstractmethod
    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool: ...

    # The existing session (of this type) a caller should resume — None
    # means there isn't one yet and a new session should be created
    # instead. A specific already-known session_id never needs to reach
    # here to stay reachable: writing to it directly (is_valid_write_target)
    # never depends on it being the one resolve() would pick.
    @abstractmethod
    def resolve_session(self, session_manager: "ChatSessionManager", username: str, project_id: str) -> dict | None: ...

    # Whether this specific session may currently be written to (a chat
    # turn or manual action applied to it) — `active_session` is whatever
    # currently occupies this type's active-slot pool, if any.
    @abstractmethod
    def is_valid_write_target(self, session: dict, active_session: dict | None) -> bool: ...

    # The state a brand-new session of this type should start in — each
    # strategy resolves this itself via project_service, using whatever
    # scope actually applies to it (the calling user's own last live
    # transition for live, always the automaton's init state for test).
    @abstractmethod
    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str: ...

    # Which project revision (published vs. draft) a session of this type
    # runs against.
    @abstractmethod
    def revision_for(self, project_service: "ProjectService", project_id: str) -> int: ...

    # The "on-enter" payload a brand-new session of this type should report
    # to the client, if any — None when starting_state() resumes an
    # already-ongoing state rather than genuinely entering one.
    @abstractmethod
    def on_enter_for_new_session(self, automaton: "Automaton") -> dict | None: ...


class LiveSessionStrategy(SessionTypeStrategy):
    type_name = 'live'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        if session["datetime_end"] is None:
            return False
        return now - session["datetime_end"] >= open_window

    def resolve_session(self, session_manager: "ChatSessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_valid_write_target(self, session: dict, active_session: dict | None) -> bool:
        return (
            active_session is not None and active_session["id"] == session["id"]
            and session["channel"] == Session().channel
        )

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        _, state = project_service.get_automaton_and_state(project_id, type=self.type_name, username=username)
        return state.key

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_published_revision(project_id)

    def on_enter_for_new_session(self, automaton: "Automaton") -> dict | None:
        return None


class TestSessionStrategy(SessionTypeStrategy):
    type_name = 'test'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return False

    def resolve_session(self, session_manager: "ChatSessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_valid_write_target(self, session: dict, active_session: dict | None) -> bool:
        return True

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        revision = self.revision_for(project_service, project_id)
        automaton = project_service.get_automaton(project_id, revision)
        return automaton.init_action.target

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_draft_revision(project_id)

    def on_enter_for_new_session(self, automaton: "Automaton") -> dict | None:
        return automaton.init_action.on_enter


class PreviewSessionStrategy(SessionTypeStrategy):
    type_name = 'preview'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return False

    def resolve_session(self, session_manager: "ChatSessionManager", username: str, project_id: str) -> dict | None:
        return session_manager.get_active_session(username, project_id, type=self.type_name)

    def is_valid_write_target(self, session: dict, active_session: dict | None) -> bool:
        return True

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        revision = self.revision_for(project_service, project_id)
        automaton = project_service.get_automaton(project_id, revision)
        return automaton.init_action.target

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        return project_service.get_published_revision(project_id)

    def on_enter_for_new_session(self, automaton: "Automaton") -> dict | None:
        return automaton.init_action.on_enter


class ImportedSessionStrategy(SessionTypeStrategy):
    type_name = 'imported'

    def is_expired(self, session: dict, now: datetime, open_window: timedelta) -> bool:
        return True

    def resolve_session(self, session_manager: "ChatSessionManager", username: str, project_id: str) -> dict | None:
        raise NotImplementedError(
            "An imported session is never resolved-or-created — it only ever exists via import."
        )

    def is_valid_write_target(self, session: dict, active_session: dict | None) -> bool:
        return False

    def starting_state(self, project_service: "ProjectService", project_id: str, username: str) -> str:
        raise NotImplementedError(
            "An imported session's state comes from the imported file, never resolved fresh."
        )

    def revision_for(self, project_service: "ProjectService", project_id: str) -> int:
        raise NotImplementedError(
            "An imported session's revision is stamped at import time, never resolved fresh."
        )

    def on_enter_for_new_session(self, automaton: "Automaton") -> dict | None:
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
