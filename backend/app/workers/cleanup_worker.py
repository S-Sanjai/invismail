import asyncio

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.inbox import Inbox
from app.models.email import Email
from app.services.storage_service import ATTACHMENT_DIR


import time

from app.models.attachment import Attachment


async def cleanup_orphaned_attachment_files(
    grace_period_seconds=3600,
):
    """
    Remove attachment files that are not referenced by PostgreSQL.

    The grace period prevents deleting files that are currently
    being written by an active email-processing operation.
    """

    if not ATTACHMENT_DIR.exists():
        return

    async with SessionLocal() as db:

        result = await db.execute(
            select(Attachment.storage_path)
        )

        referenced_paths = {
            Path(storage_path).resolve()
            for storage_path in result.scalars().all()
        }

    now = time.time()
    removed = 0

    for file_path in ATTACHMENT_DIR.iterdir():

        if not file_path.is_file():
            continue

        try:
            if now - file_path.stat().st_mtime < grace_period_seconds:
                continue

            if file_path.resolve() not in referenced_paths:
                file_path.unlink(missing_ok=True)
                removed += 1

        except OSError:
            print(
                f"Could not inspect/remove attachment: {file_path}"
            )

    if removed:
        print(f"Removed {removed} orphaned attachment files.")



async def cleanup_expired_inboxes():

    attachment_paths = []

    async with SessionLocal() as db:

        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(Inbox).where(
                Inbox.expires_at <= now
            ).limit(100)
        )

        expired_inboxes = result.scalars().all()

        for inbox in expired_inboxes:

            # Find all emails belonging to this inbox.
            email_result = await db.execute(
                select(Email).where(
                    Email.inbox_id == inbox.id
                )
            )

            emails = email_result.scalars().all()

            for email in emails:

                # Collect attachment paths before deleting DB records.
                attachment_result = await db.execute(
                    select(Attachment).where(
                        Attachment.email_id == email.id
                    )
                )

                attachments = attachment_result.scalars().all()

                for attachment in attachments:
                    attachment_paths.append(
                        Path(attachment.storage_path)
                    )

            # Delete inbox and its related database records.
            await db.delete(inbox)

        # Commit all database deletions first.
        try:
            await db.commit()

        except Exception:
            await db.rollback()
            raise

    # Only after successful commit, delete physical attachment files.
    for path in attachment_paths:
        try:
            await asyncio.to_thread(
                path.unlink,
                missing_ok=True,
            )
        except OSError as e:
            print(f"Could not delete attachment {path}: {e}")

    print(
        f"Deleted {len(expired_inboxes)} expired inboxes "
        f"and cleaned up {len(attachment_paths)} attachment files."
    )


async def main():

    print("InvisMail cleanup Worker Stared")

    while True:
        try:
            await cleanup_expired_inboxes()
            await cleanup_orphaned_attachment_files()

        except Exception as e:
            print(f"Cleanup error: {e}")

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())