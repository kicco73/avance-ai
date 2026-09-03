from __future__ import annotations

import argparse
import asyncio

from config import AppConfig
from db import Db
from job import JobService
from jobs import NullBroadcaster
from notification.notification_service import NotificationService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test email via the configured NotificationService.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    args = parser.parse_args()

    config = AppConfig()
    # Never started: this script only ever submits one immediate job,
    # so no hibernated task of the real deployment gets claimed by it.
    job_service = JobService(max_concurrent=1, broadcaster=NullBroadcaster(), db=Db(config.database_url))
    service = NotificationService(config.notification_service_config, job_service)
    await service.send_mail(
        to=args.to,
        subject="Avance NotificationService smoke test",
        body_md="This is a **test email** sent from `send_test_email.py`.",
    )
    print(f"Sent test email to {args.to}.")


if __name__ == "__main__":
    asyncio.run(main())
