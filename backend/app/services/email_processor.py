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
from app.services.storage_service import (
    save_attachment,
    safe_delete_spool_file,
    delete_spool_file,
    SPOOL_DIR,
)


logger = logging.getLogger(__name__)


def _spool_dlq_retention_seconds():
    # Local helper not needed here, but keep module env access consistent.
    return int(os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600"))


async def _delete_spool(path):
    await asyncio.to_thread(
        safe_delete_spool_file,
        path,
        missing_ok=True,
    )


async def process_email_event(event_id: str, data: dict):
    recipient = data["recipient"].lower()
    spool_path = Path(data["spool_path"])

    # 1-3. Duplicate check BEFORE reading the spool file or validating inbox.
    async with SessionLocal() as db:
        existing_result = await db.execute(
            select(Email).where(
                Email.source_event_id == event_id
            )
        )
        existing_email = existing_result.scalar_one_or_none()

    if existing_email is not None:
        # Considered successfully processed. Clean up leftover spool file.
        try:
            await _delete_spool(spool_path)
        except Exception:
            logger.exception("Could not delete spool file: %s", spool_path)
        logger.info("Duplicate event %s already processed; skipping.", event_id)
        return

    # Only read/parse spool when no existing DB record.
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

        # Re-check for duplicate inside the transaction (race safety).
        existing = await db.execute(
            select(Email).where(
                Email.source_event_id == event_id
            )
        )

        if existing.scalar_one_or_none():
            await db.rollback()
            try:
                await _delete_spool(spool_path)
            except Exception:
                logger.exception("Could not delete spool file: %s", spool_path)
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
            # Verify a duplicate actually exists.
            verify_result = await db.execute(
                select(Email).where(
                    Email.source_event_id == event_id
                )
            )
            if verify_result.scalar_one_or_none() is not None:
                try:
                    await _delete_spool(spool_path)
                except Exception:
                    logger.exception("Could not delete spool file: %s", spool_path)
                logger.info("Duplicate source_event_id %s ignored.", event_id)
                return
            raise

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
            verify_result = await db.execute(
                select(Email).where(
                    Email.source_event_id == event_id
                )
            )
            if verify_result.scalar_one_or_none() is not None:
                try:
                    await _delete_spool(spool_path)
                except Exception:
                    logger.exception("Could not delete spool file: %s", spool_path)
                logger.info("Duplicate source_event_id %s ignored.", event_id)
                return
            raise

        await redis_client.publish(
            f"inbox:{inbox.id}:events",
            str(email.id),
        )

    # After successful DB commit, delete spool file.
    try:
        await asyncio.to_thread(
            safe_delete_spool_file,
            spool_path,
            missing_ok=True,
        )
    except Exception:
        logger.exception("Could not delete spool file: %s", spool_path)

    logger.info("Email saved for recipient %s", recipient)