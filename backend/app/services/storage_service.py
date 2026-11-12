
import os
import uuid

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

ATTACHMENT_DIR = ROOT / "storage" / "attachments"

MAX_ATTACHMENT_SIZE = int(
    os.getenv("MAX_ATTACHMENT_SIZE_BYTES", 5 * 1024 * 1024)
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
