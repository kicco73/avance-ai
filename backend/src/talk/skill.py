"""Talk, as something the platform finds rather than builds.

Nothing outside this package names Talk. What speaks is reached the way
everything on the Bus is reached: a message of a type, and whoever
registered for it. Publishing `output.speech` and taking back
`output.audio` mirrors Listen's `input.audio` -> `input.text` exactly,
and a build without this package simply has nobody registered — which
the publisher learns from the posting itself, never by asking first.

Configured or not is two objects, not a branch: `_Talk` registers a
synthesizer and a route; `_NoTalk` registers neither, and the skill's
own Manage services section says it is off either way.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.wiring import construct
from system.bus import OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill
from talk import config as talk_config
from talk.audio_stream import AudioStream
from talk.talk_service import TalkService

logger = LoggerFactory.get_logger(__name__)


class _NoTalk:

    def __init__(self, providers: list) -> None:
        self._providers = providers

    def install(self) -> None:
        logger.info("talk-service is not enabled — no audio.")

    def uninstall(self) -> None:
        pass

    def install_controller(self, controllers: list) -> None:
        pass


class _Talk(_NoTalk):

    def __init__(self, providers: list) -> None:
        super().__init__(providers)
        self._service = TalkService.from_config(providers)

    def install(self) -> None:
        bus.subscribe(OUTPUT_SPEECH, self.speak)
        logger.info("talk-service started with %d provider(s).", len(self._providers))

    def uninstall(self) -> None:
        bus.unsubscribe(OUTPUT_SPEECH, self.speak)

    async def speak(self, message: bus.Message) -> None:
        """Starts generating one reply's spoken text and publishes the
        generation itself as `output.audio_stream` on the same envelope —
        the requester takes it back by origin and drains it chunk by
        chunk, never holding a reference to this package. A build where
        nobody takes it still warms the store, which is how the web's own
        audio route gets it (see talk_controller.TalkController)."""
        text = str(message.body)
        self._service.start(text)
        await bus.publish(message.converted(OUTPUT_AUDIO_STREAM, AudioStream(self._service, text), mime="audio/wav"))

    def install_controller(self, controllers: list) -> None:
        from talk.talk_controller import TalkController

        core = bus.collect(POINT_CORE_SERVICES, {})
        controllers.append(construct(TalkController, {**core, "talk_service": self._service}))


_INSTALLATIONS = {False: _NoTalk, True: _Talk}


class TalkSkill(Skill):

    key = "talk"
    ui_label = "Talk"
    ui_description = "Text to speech."
    project_declarable = True

    def __init__(self) -> None:
        self._providers = []
        self._installed = _NoTalk([])

    def start_service(self, raw: dict, path: Path) -> None:
        self._providers = talk_config.parse(raw, path)
        self._installed = _INSTALLATIONS[bool(self._providers)](self._providers)
        self._installed.install()

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(talk_config.public_fields(self._providers))

    def register_controllers(self, controllers: list) -> None:
        self._installed.install_controller(controllers)

    def stop(self) -> None:
        self._installed.uninstall()
