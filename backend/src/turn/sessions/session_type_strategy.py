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
    def resolve_session(self, session_manager: "SessionManager", username: str, project_id: str) -> dict | None: ...

    # Whether this session is the one this type's active-slot pool
    # currently holds — `active_session` is whatever occupies that pool,
    # if any. Deliberately channel-free: it is the half of writability
    # that has nothing to do with where a caller is speaking from, and
    # it is what every session payload reports (see TurnService.
    # _session_payload's "current"). Session listings, titles, comments
    # and the close button all go through here, and none of them is a
    # channel at all.
    @abstractmethod
    def is_current(self, session: dict, active_session: dict | None) -> bool: ...

    # XXX Compiled automaton requirement - do not touch.
    # XXX Which channel the caller is speaking on, for sessions of this
    # type — and None for the types where the question does not arise.
    # Only a live session is a conversation with somebody: a test,
    # preview or imported one is never reached from WhatsApp or from
    # anywhere else, and asking who is speaking would force every editor
    # route that opens one to answer.
    @abstractmethod
    def caller_channel(self) -> str | None: ...

    # XXX Compiled automaton requirement - do not touch.
    # XXX Whether this specific session may be written to (a chat turn or
    # manual action applied to it) *from `channel`*. The channel is an
    # argument and not a read of the ambient WebSession().channel on purpose:
    # only the three conversation operations that authorise a write ever
    # ask this, and each of them runs inside a channel that knows its own
    # name. Reading it here instead would force every caller to have one.
    @abstractmethod
    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool: ...

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

    # The "task" payload a brand-new session of this type should report
    # to the client, if any — None when starting_state() resumes an
    # already-ongoing state rather than genuinely entering one.
    @abstractmethod
    def task_for_new_session(self, automaton: "Automaton") -> dict | None: ...

    # Where a session entering cold starts: the automaton's own
    # init_action, target state plus its task payload (None if it
    # declares none) — shared by every strategy that ever starts a
    # session at project boot rather than resuming an ongoing one (test
    # and preview always; live too, under new-session-strategy: restart —
    # see LiveSessionStrategy below). The one centralized place this
    # project's "restart" behavior (also what the Run panel's own Clear
    # button gets, by deleting a test session and creating a fresh one)
    # comes from.
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
        # Raises when nobody declared one, rather than defaulting: a live
        # session cannot be opened, resumed or written to by a caller who
        # cannot say where they are speaking from. Each channel names itself.
        return WebSession().channel

    def is_valid_write_target(self, session: dict, active_session: dict | None, channel: str) -> bool:
        # A live session belongs to exactly one channel for its whole
        # life: whoever opened it. Another channel writing to it would
        # interleave two conversations into one transcript, so it has to
        # supersede the session instead (see SessionManager.
        # acquire_exclusive_session's "channel-switch").
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

    # Independent of `open_window` (that's live's configured value) — a
    # test session's own close_reason is never worth a full AI report
    # (SessionReportScheduler skips non-'live' sessions outright), so a
    # short, fixed idle window is enough: reclaim it quickly rather than
    # ever leaving it open indefinitely.
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
