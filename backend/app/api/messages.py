
import uuid
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_authenticated_inbox

from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment


router = APIRouter(
    prefix="/api/v1",
    tags=["Messages"],
)


@router.get("/inboxes/me/messages")
async def list_messages(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    query = select(Email).where(
        Email.inbox_id == inbox.id
    )

    total = await db.scalar(
        select(func.count()).select_from(query.subquery())
    )

    result = await db.execute(
        query
        .order_by(Email.received_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    emails = result.scalars().all()

    return {
        "items": [
            {
                "id": str(email.id),
                "sender": email.sender,
                "subject": email.subject,
                "received_at": email.received_at,
                "is_read": email.is_read,
            }
            for email in emails
        ],
        "page": page,
        "limit": limit,
        "total": total or 0,
    }


@router.get("/messages/{message_id}")
async def read_message(
    message_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Email).where(
            Email.id == message_id,
            Email.inbox_id == inbox.id,
        )
    )

    email = result.scalar_one_or_none()

    if email is None:
        raise HTTPException(
            status_code=404,
            detail="Message not found",
        )

    email.is_read = True

    attachment_result = await db.execute(
        select(Attachment).where(
            Attachment.email_id == email.id
        )
    )

    attachments = attachment_result.scalars().all()

    await db.commit()

    return {
        "id": str(email.id),
        "sender": email.sender,
        "recipient": email.recipient,
        "subject": email.subject,
        "text_body": email.text_body,
        "html_body": email.html_body,
        "received_at": email.received_at,
        "is_read": email.is_read,
        "attachments": [
            {
                "id": str(item.id),
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.size,
            }
            for item in attachments
        ],
    }



@router.delete("/messages/{message_id}", status_code=204)
async def delete_message(
    message_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Email).where(
            Email.id == message_id,
            Email.inbox_id == inbox.id,
        )
    )

    email = result.scalar_one_or_none()

    if email is None:
        raise HTTPException(
            status_code=404,
            detail="Message not found",
        )

    # 1. Collect attachment paths.
    attachment_result = await db.execute(
        select(Attachment).where(
            Attachment.email_id == email.id
        )
    )

    attachments = attachment_result.scalars().all()

    attachment_paths = [
        Path(attachment.storage_path)
        for attachment in attachments
    ]

    # 2. Delete the email and related DB records.
    await db.delete(email)

    # 3. Commit database changes.
    await db.commit()

    # 4. Delete physical files after successful commit.
    import logging

    logger = logging.getLogger(__name__)

    for path in attachment_paths:
        try:
            await asyncio.to_thread(
                path.unlink,
                missing_ok=True,
            )
        except OSError:
            logger.exception(
                "Could not delete attachment file: %s",
                path,
            )
