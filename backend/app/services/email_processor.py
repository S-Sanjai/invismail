import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.redis import redis_client
from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment

from app.services.email_parser import parse_email
from app.services.storage_service import save_attachment


logger = logging.getLogger(__name__)


async def process_email_event(event_id: str, data: dict):
    recipient = data["recipient"].lower()
    spool_path = Path(data["spool_path"])

    raw_data = await asyncio.to_thread(spool_path.read_bytes)

    parsed = parse_email(raw_data)

    async with SessionLocal() as db:
        result = await db.execute(
            select(Inbox).where(
                Inbox.email_address == recipient,
                Inbox.status == "active",
                Inbox.is_active.is_(True),
                Inbox.expires_at > datetime.now(timezone.utc),
            )
        )

        inbox = result.scalar_one_or_none()

        if inbox is None:
            raise ValueError("Inbox no longer exists or has expired")

        existing = await db.execute(
            select(Email).where(
                Email.source_event_id == event_id
            )
        )

        if existing.scalar_one_or_none():
            logger.info("Duplicate event %s already processed; skipping.", event_id)
            return

        email = Email(
            inbox_id=inbox.id,
            source_event_id=event_id,
            sender=parsed["sender"],
            recipient=recipient,
            subject=parsed["subject"],
            text_body=parsed["text_body"],
            html_body=parsed["html_body"],
            message_date=parsed["message_date"],
            raw_size_bytes=len(raw_data),
        )

        db.add(email)

        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.info("Duplicate source_event_id %s ignored.", event_id)
            return

        for item in parsed["attachments"]:
            storage_path = await asyncio.to_thread(
                save_attachment,
                str(email.id),
                item["filename"],
                item["content"],
            )

            attachment = Attachment(
                email_id=email.id,
                filename=item["filename"],
                content_type=item["content_type"],
                storage_path=storage_path,
                size=len(item["content"]),
            )

            db.add(attachment)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.info("Duplicate source_event_id %s ignored.", event_id)
            return

        await redis_client.publish(
            f"inbox:{inbox.id}:events",
            str(email.id),
        )

    # Keep the raw spool file until the database commit succeeds.
    try:
        await asyncio.to_thread(spool_path.unlink, missing_ok=True)
    except OSError:
        logger.exception("Could not delete spool file: %s", spool_path)

    logger.info("Email saved for recipient %s", recipient)
