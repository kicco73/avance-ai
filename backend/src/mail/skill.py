from __future__ import annotations

from pathlib import Path

from system import bus
from system.bus import TOOL_SEND_MAIL, POINT_CORE_SERVICES
from system.logging_factory import LoggerFactory
from system.skills import Skill
from mail import config as mail_config
from mail.mail_service import MailService

logger = LoggerFactory.get_logger(__name__)


class MailSkill(Skill):

    key = "mail"
    ui_label = "Mail"
    ui_description = "Sends email on behalf of a project."
    project_declarable = True

    def requirements(self) -> list[str]:
        return ["aiosmtplib>=5.0", "markdown>=3.7"]

    def __init__(self) -> None:
        self._config = None
        self._service = None

    def start_service(self, raw: dict, path: Path) -> None:
        self._config = mail_config.parse(raw, path)
        if self._config is None:
            logger.info("mail-service is not enabled — task.send_mail can't run.")
            return

        bus.subscribe(TOOL_SEND_MAIL, self.on_mail_send)
        logger.info("mail-service started.")

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(mail_config.public_fields(self._config))

    async def on_mail_send(self, message: bus.Message) -> None:
        body = message.body
        self._mail_service().enqueue_mail(body["to"], body["subject"], body["body_md"])

    def _mail_service(self) -> MailService:
        # Built on the first mail rather than at start: MailService needs
        # the scheduler, which does not exist at boot. By the time
        # anything publishes TOOL_SEND_MAIL the core is composed, so this is
        # the whole difference between "needs a core object" and "takes
        # one as a parameter" (see bus.POINT_CORE_SERVICES).
        for service in filter(None, [self._service]):
            return service
        for config in filter(None, [self._config]):
            self._service = MailService(config, bus.collect(POINT_CORE_SERVICES, {})["scheduler_service"])
            return self._service
        raise RuntimeError("mail-service is not enabled — task.send_mail can't run.")

    def stop(self) -> None:
        bus.unsubscribe(TOOL_SEND_MAIL, self.on_mail_send)

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        """A project that calls task.send_mail cannot run in a build without
        this package: the call would find nobody registered for TOOL_SEND_MAIL
        and raise where the automaton expects a mail to go out."""
        return any("task.send_mail" in (action.task or "") for action in _actions(automaton))


def _actions(automaton):
    return [action for state in automaton.states.values() for action in state.actions]
