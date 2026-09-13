from __future__ import annotations

from pathlib import Path

from system.logging_factory import LoggerFactory
from system.skills import Skill
from whatsapp import config as whatsapp_config
from whatsapp.whatsapp_service import NoWhatsApp, installation

logger = LoggerFactory.get_logger(__name__)


class WhatsAppSkill(Skill):

    ui_label = "WhatsApp"
    ui_description = "Chat channel over the WhatsApp Cloud API."
    project_declarable = True

    def docs(self) -> dict[str, str]:
        return {"project-specs": "PROJECT_SPECS.md"}

    def __init__(self) -> None:
        self._config = None
        self._installed = NoWhatsApp(None)

    def start_service(self, raw: dict, path: Path) -> None:
        self._config = whatsapp_config.parse(raw, path)
        self._installed = installation(self._config)
        self._installed.install()

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(whatsapp_config.public_fields(self._config))

    def register_controllers(self, controllers: list) -> None:
        self._installed.listen(controllers)

    async def stop(self) -> None:
        await self._installed.uninstall()

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        return any("task.whatsapp" in (action.task or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
