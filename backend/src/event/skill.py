from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_CORE_SERVICES, POINT_TRIGGER_NAMESPACES
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class EventSkill(Skill):

    key = "event"
    ui_label = "Event"
    ui_description = "Re-evaluates a project when another one it watches moves."
    project_declarable = True

    def __init__(self) -> None:
        from event.event_namespace import EventNamespace

        self._service = None
        self._namespace = EventNamespace()

    def start(self, raw: dict, path: Path) -> None:
        super().start(raw, path)
        bus.contribute(POINT_TRIGGER_NAMESPACES, self._declare)

    def _declare(self, namespaces) -> None:
        namespaces.declare(self._namespace)

    def docs(self) -> dict[str, str]:
        return {"project-specs": "PROJECT_SPECS.md"}

    def register_controllers(self, controllers: list) -> None:
        from event.event_service import EventService

        core = bus.collect(POINT_CORE_SERVICES, {})
        self._service = EventService(
            core["db"], core["project_service"], core["scheduler_service"],
            core["namespace_factory"],
            ai_service=core["ai_live_service"],
        )
        self._service.register()
        logger.info("event started — a project that watches another one hears it move.")

    def stop(self) -> None:
        bus.withdraw(POINT_TRIGGER_NAMESPACES, self._declare)
        for service in filter(None, [self._service]):
            service.unregister()
        self._service = None

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        return any("event." in (action.trigger or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
