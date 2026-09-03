from __future__ import annotations

from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import urlsplit

import markdown

from config import NotificationServiceConfig
from job import JobService
from logging_factory import LoggerFactory
from notification.errors import NotificationError
from notification.send_mail_job import SendMailJob

logger = LoggerFactory.get_logger(__name__)


class NotificationService:

    _SCHEME_DEFAULTS = {
        "smtp": (587, False),
        "smtps": (465, True),
    }

    def __init__(self, config: NotificationServiceConfig | None, job_service: JobService) -> None:
        # None whenever this deployment's own .config.yml declares no
        # notification-service section — always constructed regardless
        # (actuator.send_mail is the only caller, and may never fire), but
        # any actual attempt to send/enqueue a mail then raises, rather
        # than failing app boot for a feature nothing may ever use.
        self._config = config
        self._job_service = job_service
        if config is not None:
            self._username = config.username
            self._password = config.password
            self._from_name = config.from_name
            self._timeout_seconds = config.timeout_seconds
            self._hostname, self._port, self._implicit_tls = self._parse_url(config.url)

    @classmethod
    def _parse_url(cls, url: str) -> tuple[str, int, bool]:
        parsed = urlsplit(url)
        if parsed.scheme not in cls._SCHEME_DEFAULTS:
            raise ValueError(
                f"notification-service.url has unsupported scheme {parsed.scheme!r} — expected 'smtp' or 'smtps'."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "notification-service.url must not contain credentials — use the username/password fields instead."
            )
        if not parsed.hostname:
            raise ValueError("notification-service.url is missing a host.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("notification-service.url must not contain a path, query, or fragment.")
        default_port, implicit_tls = cls._SCHEME_DEFAULTS[parsed.scheme]
        return parsed.hostname, parsed.port or default_port, implicit_tls

    def _build_message(self, to: str, subject: str, body_md: str) -> MIMEMultipart:
        message = MIMEMultipart("alternative")
        message["Subject"] = Header(subject, "utf-8").encode()
        from_address = formataddr((self._from_name, self._username)) if self._from_name else self._username
        message["From"] = from_address
        message["To"] = to
        message.attach(MIMEText(body_md, "plain", "utf-8"))
        message.attach(MIMEText(markdown.markdown(body_md), "html", "utf-8"))
        return message

    async def send_mail(self, to: str, subject: str, body_md: str) -> None:
        job = self._build_send_mail_job(to, subject, body_md)
        self._job_service.submit(job)
        await self._job_service.wait_for(job)

        if job.exception is not None:
            raise NotificationError(f"Failed to send email to {to!r}.") from job.exception

        logger.info(f"Sent email to {to!r} (subject: {subject!r}).")

    def enqueue_mail(self, to: str, subject: str, body_md: str) -> None:
        self._job_service.submit(self._build_send_mail_job(to, subject, body_md))

    def _build_send_mail_job(self, to: str, subject: str, body_md: str) -> SendMailJob:
        if self._config is None:
            raise NotificationError(
                "No 'notification-service' section in .config.yml — actuator.send_mail can't run."
            )
        message = self._build_message(to, subject, body_md)
        return SendMailJob(
            self._hostname, self._port, self._implicit_tls, self._username, self._password,
            self._timeout_seconds, message,
        )
