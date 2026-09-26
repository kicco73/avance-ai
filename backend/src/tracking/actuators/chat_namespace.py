from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from typing import Protocol, TYPE_CHECKING

from automaton.automaton import JsSnippet
from system.logging_factory import LoggerFactory
from system import bus
from system.bus import OUTPUT_CHART, OUTPUT_PROGRESS, SESSION_TAKEN_OVER, UI_NOTIFICATION, Message
from system.web_session import WebSession

from .actuator_set import _run_sync

if TYPE_CHECKING:
    from tracking.actuators.factory import TaskNamespaceFactory

logger = LoggerFactory.get_logger(__name__)


def _table_cell(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def markdown_table(table: dict) -> str:
    columns = {_table_cell(name): [_table_cell(value) for value in values] for name, values in table.items()}
    if not columns:
        return ""
    height = max(len(values) for values in columns.values())
    rows = [[values[i] if i < len(values) else "" for values in columns.values()] for i in range(height)]
    lines = ["| " + " | ".join(cells) + " |" for cells in [list(columns), *rows]]
    return "\n".join([lines[0], "|" + " --- |" * len(columns), *lines[1:]])


class ReplySink(Protocol):
    def write(self, text: str) -> None: ...
    def take(self) -> str: ...


class MutedReply:
    def write(self, text: str) -> None:
        return None

    def take(self) -> str:
        return ""


class ChatNamespace(ABC):
    """`celebrate`/`notify`/`show`/`show_media` compile straight to the
    frontend's own taskActions.js locals of the same name — no real-world
    side effect at call time, just a JsSnippet, so all four behave
    identically regardless of a test session's "Run actuators" toggle.
    `switch_to_ai`
    is the same: no real-world side effect to suppress (nobody is
    paged), so unlike `switch_to_human` it's one concrete method here,
    not a Live/Fake pair. Only `switch_to_human` (real side effect —
    pages a person) is each subclass's own concern. Reachable only from
    an action's own `on-exit:` script (see IdentifierRegistry.for_on_exit)."""

    def __init__(self, project_id: str, factory: "TaskNamespaceFactory | None" = None) -> None:
        self._project_id = project_id
        self._factory = factory
        self._session_id: int | None = None
        self._reply: ReplySink = MutedReply()

    def with_session(self, session_id: int) -> "ChatNamespace":
        """A copy of this namespace bound to the session whose on-exit
        script is actually running — never mutates `self`, so the
        long-lived instance a factory hands out stays reusable."""
        bound = copy.copy(self)
        bound._session_id = session_id
        return bound

    def with_reply(self, reply: ReplySink) -> "ChatNamespace":
        bound = copy.copy(self)
        bound._reply = reply
        return bound

    def write(self, body_md: str) -> None:
        self._reply.write(body_md)

    def write_table(self, table: dict) -> None:
        for text in filter(None, [markdown_table(table)]):
            self._reply.write(text)

    def celebrate(self) -> JsSnippet | None:
        return JsSnippet("celebrate()")

    def notify(self, title: str, body_md: str, icon_url: str | None = None) -> JsSnippet | None:
        arguments = [title, body_md, *filter(None, [icon_url])]
        return JsSnippet(f"notify({', '.join(json.dumps(argument) for argument in arguments)})")

    def show(self, body_md: str) -> JsSnippet | None:
        return JsSnippet(f"show({json.dumps(body_md)})")

    def show_media(self, url: str) -> JsSnippet | None:
        return JsSnippet(f"show_media({json.dumps(url)})")

    def clear(self) -> JsSnippet | None:
        return JsSnippet("clear()")

    def _publish(self, message_type: str, body: dict) -> None:
        if self._factory is None or self._session_id is None:
            return
        _run_sync(bus.publish(Message(
            type=message_type, username=WebSession().user, session_id=self._session_id,
            project_id=self._project_id, body=body,
        )))

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
        _run_sync(bus.publish(Message(
            type=UI_NOTIFICATION, username=WebSession().user, session_id=self._session_id,
            project_id=self._project_id, body={"task": snippet_text},
        )))

    def chart(self, title: str, *series: dict, max_scale: float | None = None) -> None:
        """Publishes output.chart the same way push_notification publishes
        ui.notification — best-effort and silent, whether or not the
        session's socket is watched by anyone.

        One `{'line': ..., 'value': ...}` per bar, written as its own
        argument so a chart reads down the page as the bars it draws.
        `max_scale` is the full-scale value every bar is measured
        against: without it the chart scales to its own values, so the
        largest bar is always full — right for comparing lines to each
        other, wrong for a score out of a known maximum, which is what
        `max_scale=100` says."""
        if self._factory is None or self._session_id is None:
            return
        _run_sync(bus.publish(Message(
            type=OUTPUT_CHART, username=WebSession().user, session_id=self._session_id,
            project_id=self._project_id,
            body={"title": title, "series": list(series), "max_scale": max_scale},
        )))

    def progress(self, title: str, percentage: float) -> None:
        """Publishes output.progress the same way push_notification publishes
        ui.notification — best-effort and silent, whether or not the
        session's socket is watched by anyone. Distinct from ui.progress
        (system/broadcaster.py): that one is a user-wide broadcast, this
        one is this session's own chat turn."""
        if self._factory is None or self._session_id is None:
            return
        _run_sync(bus.publish(Message(
            type=OUTPUT_PROGRESS, username=WebSession().user, session_id=self._session_id,
            project_id=self._project_id, body={"title": title, "percentage": percentage},
        )))

    @abstractmethod
    def switch_to_human(self, user_id: str) -> JsSnippet | None:
        raise NotImplementedError


class LiveChatNamespace(ChatNamespace):
    """Bound to one project directly (no dispatcher — chat has nothing
    to schedule as a background Task, unlike TaskNamespace)."""

    def switch_to_human(self, user_id: str) -> None:
        if self._factory is None or self._session_id is None:
            logger.warning("chat.switch_to_human() called outside a session context — ignored.")
            return None
        self._factory.set_human_operator(self._session_id, user_id)
        _run_sync(bus.publish(Message(
            type=SESSION_TAKEN_OVER, username=user_id, session_id=self._session_id,
            body={"project_id": self._project_id},
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
