"""Listen, as something the platform finds rather than builds.

Everything this package contributes is registered from here, and nothing
outside it names Listen: not main.py, not config.py, not the chat
controller. Which is the whole point — a build that leaves
`backend/src/listen/` out has no speech-to-text, and there is nothing
else to switch off.

What it registers:
  - its own `listen-service` section, parsed by listen/config.py
  - a decoder on the Bus for `input.audio` (listen/decoder.py)
  - its own route (listen/listen_controller.py)
  - `listen_enabled` on GET /api/skills/platform/state, read from the service itself at
    the moment the answer is built — the model loads in a background
    thread, so this is False for the first moments after boot and True
    afterwards, without anyone caching a stale answer
  - its section of Settings > Manage services
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import POINT_API_STATE, POINT_CORE_SERVICES
from automaton.project_services import OptionalService
from system.wiring import construct
from listen import config as listen_config
from listen.decoder import SpeechDecoder
from listen.listen_controller import ListenController
from listen.listen_service import ListenService
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)


class ListenSkill(Skill):

    key = "listen"
    ui_label = "Listen"
    ui_description = "Speech to text."
    project_declarable = True

    def requirements(self) -> list[str]:
        return ["faster-whisper>=1.0"]

    def __init__(self) -> None:
        self._providers = None
        self._service = None

    def start_service(self, raw: dict, path: Path) -> None:
        """Called once at boot with the configuration file as it was read.
        Absent or disabled section: nothing registers, and every question
        about speech-to-text answers "nobody" from then on."""
        self._providers = listen_config.parse(raw, path)
        if self._providers is None:
            logger.info("listen-service is not enabled — no decoder, no route.")
            return

        self._service = ListenService.from_config(self._providers)
        SpeechDecoder(self._service).register()
        bus.contribute(POINT_API_STATE, lambda payload: payload.update(
            {"listen_enabled": self._project_listen().narrow(self._service.enabled)}
        ))
        logger.info("listen-service started with %d provider(s).", len(self._providers))

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(listen_config.public_fields(self._providers))

    def register_controllers(self, controllers: list) -> None:
        for service in filter(None, [self._service]):
            controllers.append(construct(
                ListenController, {**bus.collect(POINT_CORE_SERVICES, {}), "listen_service": service}
            ))

    def _project_listen(self) -> OptionalService:
        """What the active project declared about this service — the same
        "a project can only narrow the server's own switch" the talk level
        gets in PlatformController.get_state. No active project (or none
        loadable) declares nothing, which is `optional`."""
        try:
            return bus.collect(POINT_CORE_SERVICES, {})["project_service"].get_active_automaton().services[self.key]
        except Exception:  # noqa: BLE001
            return OptionalService()
