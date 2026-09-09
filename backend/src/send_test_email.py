from __future__ import annotations

import argparse
import asyncio

from config import AppConfig
from db import Db
from broadcaster import Broadcaster
from mail import config as mail_config
from mail.mail_service import MailService
from scheduler import SchedulerService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test email via the configured MailService.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    args = parser.parse_args()

    config = AppConfig()
    mail_service_config = mail_config.parse(config.raw, config.path)
    if mail_service_config is None:
        raise SystemExit(f"{config.path}: no '{mail_config.SECTION}' section.")
    # Never started: this script only ever submits one immediate job,
    # so no hibernated task of the real deployment gets claimed by it.
    scheduler_service = SchedulerService(max_concurrent=1, broadcaster=Broadcaster(), db=Db(config.database_url))
    service = MailService(mail_service_config, scheduler_service)
    await service.send_mail(
        to=args.to,
        subject="Avance MailService smoke test",
        body_md="This is a **test email** sent from `send_test_email.py`.",
    )
    print(f"Sent test email to {args.to}.")


if __name__ == "__main__":
    asyncio.run(main())
