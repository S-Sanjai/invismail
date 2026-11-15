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
    """Delete a spool file only if its parent resolves inside SPOOL_DIR.

    The final path component is not resolved, so a symlink is unlinked
    itself rather than followed to an external target.
    """
    try:
        spool_dir = _spool_dir().resolve()
    except (TypeError, ValueError, OSError, RuntimeError):
        logger.exception("Could not resolve spool directory: %s", path)
        return False

    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        logger.exception("Could not interpret spool path: %s", path)
        return False

    if candidate.is_absolute():
        full = candidate
    else:
        full = spool_dir / candidate

    if full.name in ("", ".", ".."):
        logger.warning("Refused to delete invalid spool path: %s", path)
        return False

    parent = full.parent
    try:
        resolved_parent = parent.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Could not resolve spool parent: %s", path)
        return False

    try:
        resolved_parent.relative_to(spool_dir)
    except ValueError:
        logger.warning(
            "Refused to delete spool path outside spool dir: %s", path
        )
        return False

    target = resolved_parent / full.name

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