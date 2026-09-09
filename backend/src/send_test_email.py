from __future__ import annotations

import argparse
import asyncio

from config import AppConfig
from mail import config as mail_config
from mail.mail_service import MailService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test email via the configured MailService.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    args = parser.parse_args()

    config = AppConfig()
    mail_service_config = mail_config.parse(config.raw, config.path)
    if mail_service_config is None:
        raise SystemExit(f"{config.path}: no '{mail_config.SECTION}' section.")
    service = MailService(mail_service_config)
    await service.send_mail(
        to=args.to,
        subject="Avance MailService smoke test",
        body_md="This is a **test email** sent from `send_test_email.py`.",
    )
    print(f"Sent test email to {args.to}.")


if __name__ == "__main__":
    asyncio.run(main())
