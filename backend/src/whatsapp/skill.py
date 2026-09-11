"""WhatsApp as something the platform finds rather than builds.

Nothing outside this package names WhatsApp: not main.py, not config.py,
not the composition root. A build that leaves `backend/src/whatsapp/`
out has no webhook for Meta to call, no channel, and nothing left in the
code saying either ever existed.

Configured or not is two objects, not a branch: `_WhatsApp` registers
the webhook routes and the service that answers them; `_NoWhatsApp`
registers neither, and the skill's own Manage services section says it
is off either way.

Its controller arrives from `register_controllers`, not from
`start_service`: starting runs at boot, before the turn engine exists;
the router is assembled after (see bus.POINT_CORE_SERVICES) — the same
shape webchat uses.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.wiring import construct
from system.bus import POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill
from whatsapp import config as whatsapp_config

logger = LoggerFactory.get_logger(__name__)


class _NoWhatsApp:

    def __init__(self, config) -> None:
        self._config = config

    def install(self) -> None:
        logger.info("whatsapp-service is not enabled — no webhook, no channel.")

    async def uninstall(self) -> None:
        pass

    def install_controller(self, controllers: list) -> None:
        pass


class _WhatsApp(_NoWhatsApp):

    def __init__(self, config) -> None:
        super().__init__(config)
        self._service = None

    def install(self) -> None:
        logger.info("whatsapp-service started.")

    def install_controller(self, controllers: list) -> None:
        from whatsapp.whatsapp_controller import WhatsAppController
        from whatsapp.whatsapp_service import WhatsAppService

        core = bus.collect(POINT_CORE_SERVICES, {})
        self._service = WhatsAppService(
            self._config, core["turn_service"], core["db"], core["auth_service"],
        )
        self._service.register()
        controllers.append(construct(WhatsAppController, {**core, "whatsapp_service": self._service}))

    async def uninstall(self) -> None:
        for service in filter(None, [self._service]):
            await service.close()
        self._service = None


_INSTALLATIONS = {False: _NoWhatsApp, True: _WhatsApp}


class WhatsAppSkill(Skill):

    key = "whatsapp"
    ui_label = "WhatsApp"
    ui_description = "Chat channel over the WhatsApp Cloud API."
    project_declarable = True

    def __init__(self) -> None:
        self._config = None
        self._installed = _NoWhatsApp(None)

    def start_service(self, raw: dict, path: Path) -> None:
        self._config = whatsapp_config.parse(raw, path)
        self._installed = _INSTALLATIONS[bool(self._config)](self._config)
        self._installed.install()

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(whatsapp_config.public_fields(self._config))

    def register_controllers(self, controllers: list) -> None:
        self._installed.install_controller(controllers)

    async def stop(self) -> None:
        await self._installed.uninstall()

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        """A project that calls task.whatsapp cannot run in a build without
        this package: the message would be posted with nothing registered to
        carry it, and the call would answer False for every recipient."""
        return any("task.whatsapp" in (action.task or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
