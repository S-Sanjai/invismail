
import os
import uuid
import asyncio
from pathlib import Path

from fastapi.responses import FileResponse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_authenticated_inbox
from app.core.rate_limit import enforce_rate_limit

from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment
from app.services.storage_service import ATTACHMENT_DIR


router = APIRouter(
    prefix="/api/v1",
    tags=["Messages"],
)


@router.get("/inboxes/me/messages")
async def list_messages(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(request, "list_messages", int(os.getenv("RATE_LIMIT_LIST_MESSAGES", "60")))
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
    request: Request,
    message_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(request, "read_message", int(os.getenv("RATE_LIMIT_READ_MESSAGE", "120")))
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


async def _download_attachment(
    request: Request,
    attachment_id: uuid.UUID,
    inbox: Inbox,
    db: AsyncSession,
    message_id: uuid.UUID | None = None,
):
    await enforce_rate_limit(
        request, "download_attachment", int(os.getenv("RATE_LIMIT_DOWNLOAD_ATTACHMENT", "120"))
    )

    filters = [
        Attachment.id == attachment_id,
        Email.inbox_id == inbox.id,
    ]
    if message_id is not None:
        filters.append(Attachment.email_id == message_id)
        filters.append(Email.id == message_id)

    result = await db.execute(
        select(Attachment)
        .join(Email, Attachment.email_id == Email.id)
        .where(*filters)
    )

    attachment = result.scalar_one_or_none()

    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    storage_path = Path(attachment.storage_path).resolve()
    attachment_root = ATTACHMENT_DIR.resolve()

    try:
        storage_path.relative_to(attachment_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attachment path")

    if not storage_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file not found")

    content_type = attachment.content_type or "application/octet-stream"
    if content_type in {"text/html", "image/svg+xml"} or "html" in content_type:
        content_type = "application/octet-stream"

    safe_filename = attachment.filename.replace(" ", "_")

    return FileResponse(
        path=str(storage_path),
        media_type=content_type,
        filename=safe_filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename={safe_filename!r}",
        },
    )


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    request: Request,
    attachment_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    return await _download_attachment(request, attachment_id, inbox, db)


@router.get("/attachments/{attachment_id}/download")
async def download_attachment_alias(
    request: Request,
    attachment_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    return await _download_attachment(request, attachment_id, inbox, db)


@router.get("/messages/{message_id}/attachments/{attachment_id}")
async def download_message_attachment(
    request: Request,
    message_id: uuid.UUID,
    attachment_id: uuid.UUID,
    inbox: Inbox = Depends(get_authenticated_inbox),
    db: AsyncSession = Depends(get_db),
):
    return await _download_attachment(request, attachment_id, inbox, db, message_id)

