from __future__ import annotations

from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import urlsplit

import aiosmtplib
import markdown

from logging_factory import LoggerFactory
from mail.config import MailServiceConfig
from mail.errors import MailError

logger = LoggerFactory.get_logger(__name__)


class MailService:

    _SCHEME_DEFAULTS = {
        "smtp": (587, False),
        "smtps": (465, True),
    }

    def __init__(self, config: MailServiceConfig) -> None:
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
                f"mail-service.url has unsupported scheme {parsed.scheme!r} — expected 'smtp' or 'smtps'."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "mail-service.url must not contain credentials — use the username/password fields instead."
            )
        if not parsed.hostname:
            raise ValueError("mail-service.url is missing a host.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("mail-service.url must not contain a path, query, or fragment.")
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
        message = self._build_message(to, subject, body_md)
        try:
            await aiosmtplib.send(
                message, hostname=self._hostname, port=self._port,
                username=self._username, password=self._password,
                use_tls=self._implicit_tls, start_tls=not self._implicit_tls,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            raise MailError(f"Failed to send email to {to!r}.") from exc
        logger.info(f"Sent email to {to!r} (subject: {subject!r}).")
