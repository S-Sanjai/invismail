import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.database import SessionLocal
from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment
from app.services.storage_service import ATTACHMENT_DIR, save_attachment
from app.workers.cleanup_worker import cleanup_expired_inboxes


@pytest.mark.asyncio
async def test_expired_inbox_deletes_attachments():
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as db:
        inbox = Inbox(
            email_address=f"expired-{uuid.uuid4()}@invismail.test",
            access_token_hash="hash",
            status="active",
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(inbox)
        await db.flush()

        email = Email(
            inbox_id=inbox.id,
            source_event_id=str(uuid.uuid4()),
            sender="a@b.com",
            recipient=inbox.email_address,
            subject="test",
            raw_size_bytes=1,
        )
        db.add(email)
        await db.flush()

        storage_path = save_attachment(str(email.id), "file.txt", b"hello")
        attachment = Attachment(
            email_id=email.id,
            filename="file.txt",
            content_type="text/plain",
            storage_path=storage_path,
            size=5,
        )
        db.add(attachment)
        await db.commit()

    assert Path(storage_path).exists()

    await cleanup_expired_inboxes()

    async with SessionLocal() as db:
        from sqlalchemy import select
        res = await db.execute(select(Inbox).where(Inbox.id == inbox.id))
        assert res.scalar_one_or_none() is None

    assert not Path(storage_path).exists()