"""Talk, as something the platform finds rather than builds.

Nothing outside this package names Talk. What speaks is reached the way
everything on the Bus is reached: a message of a type, and whoever
registered for it. Publishing `output.speech` and taking back
`output.audio` mirrors Listen's `input.audio` -> `input.text` exactly,
and a build without this package simply has nobody registered — which
the publisher learns from the posting itself, never by asking first.

Configured or not is two objects, not a branch: `_Talk` registers a
synthesizer, a route and its Manage services section; `_NoTalk`
registers only the section, saying it is off.
"""
from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, POINT_CONFIG_SERVICES, POINT_CORE_SERVICES, POINT_HTTP_CONTROLLERS
from system.config_services import ui_section
from system.logging_factory import LoggerFactory
from talk import config as talk_config
from talk.audio_stream import AudioStream
from talk.talk_service import TalkService

logger = LoggerFactory.get_logger(__name__)

KEY = "talk"
UI_LABEL = "Talk"
UI_DESCRIPTION = "Text to speech."


class _NoTalk:

    def __init__(self, providers: list) -> None:
        self._providers = providers

    def install(self) -> None:
        self._contribute_config()
        logger.info("talk-service is not enabled — no audio.")

    def uninstall(self) -> None:
        pass

    def _contribute_config(self) -> None:
        bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update(
            {KEY: ui_section(UI_LABEL, UI_DESCRIPTION, talk_config.public_fields(self._providers))}
        ))


class _Talk(_NoTalk):

    def __init__(self, providers: list) -> None:
        super().__init__(providers)
        self._service = TalkService.from_config(providers)

    def install(self) -> None:
        self._contribute_config()
        bus.subscribe(OUTPUT_SPEECH, self.speak)
        bus.contribute(POINT_HTTP_CONTROLLERS, self.install_controller)
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
        controllers.append(TalkController(core["turn_service"], self._service))


_INSTALLATIONS = {False: _NoTalk, True: _Talk}
_installed = _NoTalk([])


def start(raw: dict, path: Path) -> None:
    global _installed
    providers = talk_config.parse(raw, path)
    _installed = _INSTALLATIONS[bool(providers)](providers)
    _installed.install()


def stop() -> None:
    _installed.uninstall()


def required_by(automaton, sources: dict[str, str]) -> bool:
    """Read from what the project wrote, not from the built automaton:
    `talk-enabled` defaults to true, so the flag says nothing about
    whether this project ever asked to be spoken. An explicit
    `talk-enabled: true` is the asking."""
    return any(_asks_for_speech(text) for text in filter(_is_text, sources.values()))


def _is_text(content) -> bool:
    """A project carries its attachments too, and those are bytes."""
    return isinstance(content, str)


def _asks_for_speech(text: str) -> bool:
    return any(
        line.split("#")[0].strip().replace(" ", "") == "talk-enabled:true"
        for line in text.splitlines()
    )
