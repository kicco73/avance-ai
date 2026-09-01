from __future__ import annotations

import uuid
from email.mime.multipart import MIMEMultipart

import aiosmtplib

from jobs import CancelableJob


class SendMailJob(CancelableJob):

    def __init__(
        self, hostname: str, port: int, implicit_tls: bool, smtp_username: str, smtp_password: str,
        timeout_seconds: int, message: MIMEMultipart,
    ) -> None:
        super().__init__(key=f"notification-send:{uuid.uuid4()}", username="system")
        self._hostname = hostname
        self._port = port
        self._implicit_tls = implicit_tls
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._timeout_seconds = timeout_seconds
        self._message = message
        self.exception: Exception | None = None

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        # Caught here rather than left to propagate: Job.run_next_step()
        # (do-not-touch) only preserves str(exc) on failure, and send_mail
        # needs the real exception object to chain into NotificationError.
        try:
            await aiosmtplib.send(
                self._message,
                hostname=self._hostname,
                port=self._port,
                username=self._smtp_username,
                password=self._smtp_password,
                use_tls=self._implicit_tls,
                start_tls=not self._implicit_tls,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            self.exception = exc
