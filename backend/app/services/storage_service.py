import os
import uuid
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]

ATTACHMENT_DIR = ROOT / "storage" / "attachments"

MAX_ATTACHMENT_SIZE = int(
    os.getenv("MAX_ATTACHMENT_SIZE_BYTES", 5 * 1024 * 1024)
)

SPOOL_DIR = ROOT / "storage" / "spool"

SPOOL_DLQ_RETENTION_SECONDS = int(
    os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
)

SPOOL_ORPHAN_GRACE_SECONDS = int(
    os.getenv("SPOOL_ORPHAN_GRACE_SECONDS", "3600")
)


def save_attachment(
    email_id: str,
    filename: str,
    content: bytes,
) -> str:

    if len(content) > MAX_ATTACHMENT_SIZE:
        raise ValueError("Attachment exceeds size limit")

    ATTACHMENT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Never use an email-provided filename as a path.
    attachment_id = str(uuid.uuid4())

    storage_path = (
        ATTACHMENT_DIR / attachment_id
    )

    with open(storage_path, "xb") as file:
        file.write(content)

    return str(storage_path)


def _spool_dir():
    import app.services.storage_service as _self
    value = getattr(_self, "SPOOL_DIR", SPOOL_DIR)
    return Path(value)


def safe_delete_spool_file(path, missing_ok=True):
    """Delete a spool file only if it resolves inside SPOOL_DIR."""
    try:
        spool_dir = _spool_dir().resolve()
        target = Path(path).resolve()
    except (TypeError, ValueError, OSError):
        logger.exception("Could not resolve spool path: %s", path)
        return False

    try:
        target.relative_to(spool_dir)
    except ValueError:
        logger.warning("Refused to delete spool path outside spool dir: %s", target)
        return False

    try:
        target.unlink(missing_ok=missing_ok)
        return True
    except OSError:
        logger.exception("Could not delete spool file: %s", target)
        return False


async def delete_spool_file(path, missing_ok=True):
    return await asyncio.to_thread(
        safe_delete_spool_file,
        path,
        missing_ok=missing_ok,
    )


# Alternate names for compatibility.
safe_delete_spool = safe_delete_spool_file
delete_spool = delete_spool_file
remove_spool_file = delete_spool_file
safe_delete_spool_path = safe_delete_spool_file