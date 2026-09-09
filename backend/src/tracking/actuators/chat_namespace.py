from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from automaton.automaton import JsSnippet
from logging_factory import LoggerFactory
import bus
from bus import UI_HUMAN_TAKEOVER, UI_NOTIFICATION, Message
from session import Session

from .actuator_set import _run_sync

if TYPE_CHECKING:
    from tracking.actuators.factory import TaskNamespaceFactory

logger = LoggerFactory.get_logger(__name__)


class ChatNamespace(ABC):
    """`celebrate`/`notify`/`show` compile straight to the frontend's own
    taskActions.js locals of the same name — no real-world side effect
    at call time, just a JsSnippet, so all three behave identically
    regardless of a test session's "Run actuators" toggle. `switch_to_ai`
    is the same: no real-world side effect to suppress (nobody is
    paged), so unlike `switch_to_human` it's one concrete method here,
    not a Live/Fake pair. Only `switch_to_human` (real side effect —
    pages a person) is each subclass's own concern. Reachable only from
    an action's own `on-exit:` script (see IdentifierRegistry.for_on_exit)."""

    def __init__(self, factory: "TaskNamespaceFactory | None" = None) -> None:
        # The factory that built this namespace — where switch_to_human/
        # switch_to_ai actually record the operator (see
        # TaskNamespaceFactory.set_human_operator/clear_human_operator),
        # and where push_notification reaches whatever the person has open.
        self._factory = factory
        # Bound fresh per on-exit evaluation via with_session — never
        # set any other way. None for a namespace built without a firing
        # session (e.g. a project-wide test reset) — switch_to_human/
        # switch_to_ai/push_notification are then no-ops.
        self._session_id: int | None = None

    def with_session(self, session_id: int) -> "ChatNamespace":
        """A copy of this namespace bound to the session whose on-exit
        script is actually running — never mutates `self`, so the
        long-lived instance a factory hands out stays reusable."""
        bound = copy.copy(self)
        bound._session_id = session_id
        return bound

    def celebrate(self) -> JsSnippet | None:
        return JsSnippet("celebrate()")

    def notify(self, title: str, body_md: str) -> JsSnippet | None:
        return JsSnippet(f"notify({json.dumps(title)}, {json.dumps(body_md)})")

    def show(self, body_md: str) -> JsSnippet | None:
        return JsSnippet(f"show({json.dumps(body_md)})")

    def switch_to_ai(self) -> None:
        """Hands the session back to the AI after switch_to_human — a
        no-op outside a session context (see _session_id)."""
        if self._factory is not None and self._session_id is not None:
            self._factory.clear_human_operator(self._session_id)

    def push_notification(self, snippet_text: str) -> None:
        """Pushes `snippet_text` (already-joined JsSnippet text — see
        Automaton.eval_action_on_exit) as the same ui.notification
        `task:`'s own ActionTask publishes (see
        tracking/actuators/action_task.py's own _run_next_step) —
        best-effort and silent: a no-op with no factory or session, and
        otherwise published whether or not any interface is listening."""
        if self._factory is None or self._session_id is None:
            return
        _run_sync(bus.publish(Message(type=UI_NOTIFICATION, username=Session().user, body={"task": snippet_text})))

    @abstractmethod
    def switch_to_human(self, user_id: str) -> JsSnippet | None:
        raise NotImplementedError


class LiveChatNamespace(ChatNamespace):
    """Bound to one project directly (no dispatcher — chat has nothing
    to schedule as a background Task, unlike TaskNamespace)."""

    def __init__(self, project_id: str, factory: "TaskNamespaceFactory | None" = None) -> None:
        super().__init__(factory)
        self._project_id = project_id

    def switch_to_human(self, user_id: str) -> None:
        if self._factory is None or self._session_id is None:
            logger.warning("chat.switch_to_human() called outside a session context — ignored.")
            return None
        self._factory.set_human_operator(self._session_id, user_id)
        _run_sync(bus.publish(Message(
            type=UI_HUMAN_TAKEOVER, username=user_id,
            body={"session_id": self._session_id, "project_id": self._project_id},
        )))
        return None


class FakeChatNamespace(ChatNamespace):
    """Stands in for LiveChatNamespace while a test session's own "Run
    actuators" toggle is off — mirrors FakeTaskNamespace's own
    suppress-and-report shape for its one real side effect."""

    def switch_to_human(self, user_id: str) -> JsSnippet | None:
        message = f"switch_to_human(user_id={user_id!r}) — Run actuators is off, no one was paged; the AI answers instead."
        logger.info(message)
        return self.notify("Chat (test)", message)
