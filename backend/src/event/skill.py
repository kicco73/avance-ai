from __future__ import annotations

from system import bus
from system.bus import POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class EventSkill(Skill):

    key = "event"
    ui_label = "Event"
    ui_description = "Re-evaluates a project when another one it watches moves."
    project_declarable = True

    def __init__(self) -> None:
        self._service = None

    def docs(self) -> dict[str, str]:
        return {"project-specs": "PROJECT_SPECS.md"}

    def register_controllers(self, controllers: list) -> None:
        from event.event_service import EventService

        core = bus.collect(POINT_CORE_SERVICES, {})
        self._service = EventService(
            core["db"], core["project_service"], core["scheduler_service"],
            core["namespace_factory"],
            tracking_service=core["tracking_service"],
            ai_service=core["ai_live_service"],
        )
        self._service.register()
        logger.info("event started — a project that watches another one hears it move.")

    def stop(self) -> None:
        for service in filter(None, [self._service]):
            service.unregister()
        self._service = None

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        return any("automaton." in (action.trigger or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
