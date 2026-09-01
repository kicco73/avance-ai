from __future__ import annotations

import argparse
import asyncio

from config import AppConfig
from jobs import JobQueue, NullBroadcaster
from notification.notification_service import NotificationService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test email via the configured NotificationService.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    args = parser.parse_args()

    config = AppConfig()
    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    service = NotificationService(config.notification_service_config, job_queue)
    await service.send_mail(
        to=args.to,
        subject="Avance NotificationService smoke test",
        body_md="This is a **test email** sent from `send_test_email.py`.",
    )
    print(f"Sent test email to {args.to}.")


if __name__ == "__main__":
    asyncio.run(main())
