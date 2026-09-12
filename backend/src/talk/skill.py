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
from system.bus import (
    OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, POINT_API_STATE, POINT_CORE_SERVICES, POINT_SPOKEN_REPLY,
)
from automaton.project_services import OptionalService
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
        text = str((message.body or {}).get("text") or "")
        self._service.start(text)
        await bus.publish(message.converted(
            OUTPUT_AUDIO_STREAM, {"stream": AudioStream(self._service, text)}, mime="audio/wav",
        ))

    def install_controller(self, controllers: list) -> None:
        from talk.talk_controller import TalkController

        core = bus.collect(POINT_CORE_SERVICES, {})
        controllers.append(construct(TalkController, {**core, "talk_service": self._service}))


_INSTALLATIONS = {False: _NoTalk, True: _Talk}


class TalkSkill(Skill):

    ui_label = "Talk"
    ui_description = "Text to speech."
    project_declarable = True

    def requirements(self) -> list[str]:
        return ["piper-tts>=1.6"]

    def __init__(self) -> None:
        self._providers = []
        self._installed = _NoTalk([])

    def start_service(self, raw: dict, path: Path) -> None:
        self._providers = talk_config.parse(raw, path)
        self._answer_for_itself()
        self._installed = _INSTALLATIONS[bool(self._providers)](self._providers)
        self._installed.install()

    def _answer_for_itself(self) -> None:
        """The two questions only this package can answer, registered
        before anything is installed. Contributed whether or not a
        provider is configured, because "off" is an answer and the
        absence of this package is a different one — nobody registers,
        and core concludes nothing speaks. Before, and not after, so a
        provider that fails to load does not take the answers with it.
        """
        bus.contribute(POINT_API_STATE, lambda payload: payload.update(
            {"talk_enabled": self._enabled_for_the_active_project()}
        ))
        bus.contribute(POINT_SPOKEN_REPLY, self._answer_spoken_reply)

    def _answer_spoken_reply(self, spoken) -> None:
        """Whether this build can speak a reply at all (see
        bus.POINT_SPOKEN_REPLY): the project may narrow this service,
        never turn it on. Whether the turn has any use for audio is
        somebody else's half of the answer."""
        if spoken.services[self.key].narrow(bool(self._providers)):
            spoken.ask()

    def _enabled_for_the_active_project(self) -> bool:
        """What the active project declared about this service makes of
        the server's own switch. No active project (or none loadable)
        declares nothing, which is `optional` — the same shape listen's
        own flag has."""
        try:
            declared = bus.collect(POINT_CORE_SERVICES, {})["project_service"].get_active_automaton().services[self.key]
        except Exception:  # noqa: BLE001
            declared = OptionalService()
        return declared.narrow(bool(self._providers))

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(talk_config.public_fields(self._providers))

    def register_controllers(self, controllers: list) -> None:
        self._installed.install_controller(controllers)

    def stop(self) -> None:
        self._installed.uninstall()
