import asyncio
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment
from app.services.storage_service import ATTACHMENT_DIR
from app.services import storage_service

from app.core.redis import redis_client
from app.core.logging import setup_logging

# Reuse worker constants for stream/group/DLQ.
STREAM = "email:incoming"
GROUP = "email-workers"
DLQ = "email:dead-letter-queue"

# Grace period defaults (can be overridden by env via module/global).
SPOOL_ORPHAN_GRACE_SECONDS = int(
    os.getenv("SPOOL_ORPHAN_GRACE_SECONDS", "3600")
)

SPOOL_DLQ_RETENTION_SECONDS = int(
    os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
)

# Safe deletion helpers (re-exported for compatibility).
from app.services.storage_service import (
    safe_delete_spool_file,
    delete_spool_file,
)

SPOOL_DIR = storage_service.SPOOL_DIR

logger = setup_logging("invismail.cleanup")


def _as_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def _orphan_grace_seconds():
    value = globals().get("SPOOL_ORPHAN_GRACE_SECONDS", None)
    if value is None:
        value = os.getenv("SPOOL_ORPHAN_GRACE_SECONDS", "3600")
    return int(value)


def _spool_dir():
    value = globals().get("SPOOL_DIR", None)
    if value is None:
        value = getattr(storage_service, "SPOOL_DIR", None)
    if value is None:
        value = storage_service.SPOOL_DIR
    return Path(value)


def _dlq_retention_seconds():
    value = globals().get("SPOOL_DLQ_RETENTION_SECONDS", None)
    if value is None:
        # Fall back to storage_service or env.
        value = getattr(storage_service, "SPOOL_DLQ_RETENTION_SECONDS", None)
    if value is None:
        value = os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    return int(value)


def _safe_delete(path, missing_ok=True):
    from app.services.storage_service import safe_delete_spool_file

    return safe_delete_spool_file(path, missing_ok=missing_ok)


async def cleanup_orphaned_attachment_files(
    grace_period_seconds=3600,
):
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
                await asyncio.to_thread(file_path.unlink, missing_ok=True)
                removed += 1

        except OSError:
            logger.exception(
                "Could not inspect/remove attachment: %s", file_path
            )

    if removed:
        logger.info("Removed %s orphaned attachment files.", removed)


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

            email_result = await db.execute(
                select(Email).where(
                    Email.inbox_id == inbox.id
                )
            )

            emails = email_result.scalars().all()

            for email in emails:

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

            await db.delete(inbox)

        try:
            await db.commit()

        except Exception:
            await db.rollback()
            raise

    for path in attachment_paths:
        try:
            await asyncio.to_thread(
                path.unlink,
                missing_ok=True,
            )
        except OSError as e:
            logger.exception("Could not delete attachment %s: %s", path, e)

    logger.info(
        "Deleted %s expired inboxes and cleaned up %s attachment files.",
        len(expired_inboxes),
        len(attachment_paths),
    )


async def _collect_protected_spool_paths():
    """Return a set of protected resolved spool paths, or None on failure."""
    spool_dir = _spool_dir()
    protected = set()

    try:
        # Check consumer group exists.
        try:
            groups = await redis_client.xinfo_groups(STREAM)
        except Exception:
            return None

        group_info = None
        for g in groups or []:
            if _as_text(g.get("name")) == GROUP:
                group_info = g
                break

        if group_info is None:
            # Consumer group not initialized; fail closed.
            return None

        last_delivered_id = _as_text(group_info.get("last-delivered-id"))

        # A. Pending events (PEL).
        try:
            cursor = "-"
            seen_ids = set()
            while True:
                batch = await redis_client.xpending_range(
                    STREAM,
                    GROUP,
                    min=cursor,
                    max="+",
                    count=100,
                )
                if not batch:
                    break

                new_found = False
                for entry in batch:
                    # entry may be dict or tuple
                    if isinstance(entry, dict):
                        msg_id = _as_text(entry.get("message_id") or entry.get("id"))
                    else:
                        msg_id = _as_text(entry[0]) if entry else ""
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)
                    new_found = True

                    try:
                        msgs = await redis_client.xrange(STREAM, msg_id, msg_id)
                    except Exception:
                        return None
                    if not msgs:
                        # Pending event could not be retrieved -> fail closed.
                        return None
                    for _mid, fields in msgs:
                        path = _as_text((fields or {}).get("spool_path"))
                        if not path:
                            return None
                        try:
                            protected.add(Path(path).resolve())
                        except (OSError, ValueError):
                            return None

                if len(batch) < 100:
                    break
                last_id = None
                if isinstance(batch[-1], dict):
                    last_id = _as_text(batch[-1].get("message_id") or batch[-1].get("id"))
                else:
                    last_id = _as_text(batch[-1][0]) if batch[-1] else None
                if not last_id or last_id in ("", None):
                    break
                cursor = last_id
                if not new_found:
                    break
        except Exception:
            return None

        # B. Undelivered events (id > last-delivered-id).
        try:
            if last_delivered_id:
                cursor = f"({last_delivered_id}"
            else:
                cursor = "-"
            while True:
                batch = await redis_client.xrange(
                    STREAM,
                    min=cursor,
                    max="+",
                    count=100,
                )
                if not batch:
                    break
                for msg_id, fields in batch:
                    path = _as_text((fields or {}).get("spool_path"))
                    if path:
                        try:
                            protected.add(Path(path).resolve())
                        except (OSError, ValueError):
                            pass
                if len(batch) < 100:
                    break
                last_id = _as_text(batch[-1][0])
                cursor = f"({last_id}"
        except Exception:
            return None

        # C. DLQ retention.
        try:
            cursor = "-"
            retention = _dlq_retention_seconds()
            now = time.time()
            while True:
                batch = await redis_client.xrange(
                    DLQ,
                    min=cursor,
                    max="+",
                    count=100,
                )
                if not batch:
                    break
                for msg_id, fields in batch:
                    fields = fields or {}
                    path = _as_text(fields.get("spool_path"))
                    if not path:
                        continue
                    failed_at = fields.get("failed_at")
                    expires_at = fields.get("spool_expires_at")
                    protect = False
                    if expires_at not in (None, ""):
                        try:
                            expiry = float(_as_text(expires_at))
                            protect = now < expiry
                        except (TypeError, ValueError):
                            # Invalid -> conservative protect.
                            protect = True
                    elif failed_at not in (None, ""):
                        try:
                            failed_ts = float(_as_text(failed_at))
                            expiry = failed_ts + retention
                            protect = now < expiry
                        except (TypeError, ValueError):
                            protect = True
                    else:
                        # Missing timestamps -> conservative protect.
                        protect = True

                    if protect:
                        try:
                            protected.add(Path(path).resolve())
                        except (OSError, ValueError):
                            pass
                if len(batch) < 100:
                    break
                last_id = _as_text(batch[-1][0])
                cursor = f"({last_id}"
        except Exception:
            return None

    except Exception:
        return None

    return protected


async def cleanup_orphaned_spool_files(
    grace_period_seconds=None,
):
    if grace_period_seconds is None:
        grace_period_seconds = _orphan_grace_seconds()

    spool_dir = _spool_dir()
    if not spool_dir.exists():
        return

    # Reconcile Redis references; fail closed.
    try:
        protected = await _collect_protected_spool_paths()
    except Exception:
        logger.exception("Redis reconciliation failed; skipping spool cleanup")
        return

    if protected is None:
        logger.warning(
            "Spool reconciliation could not be completed reliably; "
            "skipping .eml cleanup this cycle."
        )
        return

    now = time.time()
    removed_eml = 0
    removed_tmp = 0

    for file_path in list(spool_dir.iterdir()):
        if not file_path.is_file():
            continue

        suffix = file_path.suffix.lower()

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            logger.exception("Could not stat spool file: %s", file_path)
            continue

        if suffix == ".tmp":
            try:
                if now - mtime >= grace_period_seconds:
                    await asyncio.to_thread(
                        _safe_delete,
                        file_path,
                        missing_ok=True,
                    )
                    removed_tmp += 1
            except OSError:
                logger.exception("Could not remove spool temp file: %s", file_path)
            continue

        if suffix != ".eml":
            continue

        # .eml handling
        if now - mtime < grace_period_seconds:
            continue

        try:
            resolved = file_path.resolve()
        except OSError:
            logger.exception("Could not resolve spool file: %s", file_path)
            continue

        if resolved in protected:
            continue

        try:
            await asyncio.to_thread(
                _safe_delete,
                file_path,
                missing_ok=True,
            )
            removed_eml += 1
        except OSError:
            logger.exception("Could not remove spool file: %s", file_path)

    if removed_eml or removed_tmp:
        logger.info(
            "Removed %s orphaned spool .eml files and %s .tmp files.",
            removed_eml,
            removed_tmp,
        )


async def main():

    logger.info("InvisMail cleanup worker started")

    while True:
        try:
            await cleanup_expired_inboxes()
            await cleanup_orphaned_attachment_files()
            await cleanup_orphaned_spool_files()

        except Exception:
            logger.exception("Cleanup error")

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())