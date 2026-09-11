"""WhatsApp as something the platform finds rather than builds.

Nothing outside this package names WhatsApp: not main.py, not config.py,
not the composition root. A build that leaves `backend/src/whatsapp/`
out has no webhook for Meta to call, no channel, and nothing left in the
code saying either ever existed.

Configured or not is two objects, not a branch: `_WhatsApp` registers
the webhook routes and the service that answers them; `_NoWhatsApp`
registers only its Manage services section, saying it is off.

Registered from `install_controller`, not from `start`: start() runs at
boot, before the turn engine exists; the contributor runs when
AvanceController assembles its router, which is after (see
bus.POINT_CORE_SERVICES) — the same shape webchat uses.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.wiring import construct
from system.bus import POINT_CONFIG_SERVICES, POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.config_services import ui_section
from system.logging_factory import LoggerFactory
from whatsapp import config as whatsapp_config

logger = LoggerFactory.get_logger(__name__)

KEY = "whatsapp"
UI_LABEL = "WhatsApp"
UI_DESCRIPTION = "Chat channel over the WhatsApp Cloud API."
PROJECT_DECLARABLE = True


class _NoWhatsApp:

    def __init__(self, config) -> None:
        self._config = config

    def install(self) -> None:
        self._contribute_config()
        logger.info("whatsapp-service is not enabled — no webhook, no channel.")

    async def uninstall(self) -> None:
        pass

    def _contribute_config(self) -> None:
        bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update(
            {KEY: ui_section(UI_LABEL, UI_DESCRIPTION, whatsapp_config.public_fields(self._config))}
        ))


class _WhatsApp(_NoWhatsApp):

    def __init__(self, config) -> None:
        super().__init__(config)
        self._service = None

    def install(self) -> None:
        self._contribute_config()
        bus.contribute(POINT_HTTP_CONTROLLERS, self.install_controller)
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
_installed = _NoWhatsApp(None)


def start(raw: dict, path: Path) -> None:
    global _installed
    config = whatsapp_config.parse(raw, path)
    _installed = _INSTALLATIONS[bool(config)](config)
    _installed.install()


async def stop() -> None:
    await _installed.uninstall()


def required_by(automaton, sources: dict[str, str]) -> bool:
    """A project that calls task.whatsapp cannot run in a build without
    this package: the message would be posted with nothing registered to
    carry it, and the call would answer False for every recipient."""
    return any("task.whatsapp" in (action.task or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
