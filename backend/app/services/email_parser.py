import os
import re
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

MAX_MIME_PARTS = int(os.getenv("MAX_MIME_PARTS", "100"))
MAX_MIME_DEPTH = int(os.getenv("MAX_MIME_DEPTH", "10"))
MAX_ATTACHMENTS = int(os.getenv("MAX_ATTACHMENTS_PER_EMAIL", "25"))
MAX_TOTAL_ATTACHMENT_BYTES = int(
    os.getenv("MAX_TOTAL_ATTACHMENT_BYTES", str(25 * 1024 * 1024))
)


def _sanitize_html(html: str | None) -> str | None:
    if not html:
        return html

    # Remove dangerous embedded elements.
    html = re.sub(
        r"(?is)<(script|style|iframe|object|embed|svg)[^>]*>.*?</\1>",
        "",
        html,
    )
    html = re.sub(
        r"(?is)<(script|style|iframe|object|embed|svg)[^>]*/?>",
        "",
        html,
    )

    # Remove inline event handlers and javascript: URLs.
    html = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", html)
    html = re.sub(r"(?i)javascript:", "", html)

    return html


def parse_email(raw_data: bytes) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw_data)

    text_body = None
    html_body = None
    attachments = []
    part_count = 0
    total_attachment_bytes = 0
    attachment_count = 0

    def walk(part, depth: int):
        nonlocal text_body, html_body, part_count
        nonlocal total_attachment_bytes, attachment_count

        if depth > MAX_MIME_DEPTH:
            raise ValueError("MIME nesting depth exceeds limit")

        if part.is_multipart():
            for subpart in (part.get_payload() or []):
                walk(subpart, depth + 1)
            return

        part_count += 1
        if part_count > MAX_MIME_PARTS:
            raise ValueError("Too many MIME parts")

        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()

        payload = part.get_payload(decode=True) or b""

        # Attachment or explicitly named file.
        if disposition == "attachment" or filename:
            attachment_count += 1
            if attachment_count > MAX_ATTACHMENTS:
                raise ValueError("Too many attachments")

            total_attachment_bytes += len(payload)
            if total_attachment_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
                raise ValueError("Total attachment size exceeds limit")

            attachments.append(
                {
                    "filename": filename or "attachment.bin",
                    "content_type": content_type,
                    "content": payload,
                }
            )
            return

        # Ignore inline images and other binary resources.
        if disposition == "inline" and not content_type.startswith("text/"):
            return

        if content_type == "text/plain":
            decoded = part.get_content()
            if isinstance(decoded, str):
                text_body = decoded

        elif content_type == "text/html":
            decoded = part.get_content()
            if isinstance(decoded, str):
                html_body = _sanitize_html(decoded)

    walk(message, 0)

    sender = message.get("From", "")
    recipient = message.get("To", "")
    subject = str(message.get("Subject", ""))

    message_date = None

    try:
        message_date = (
            parsedate_to_datetime(message.get("Date"))
            if message.get("Date")
            else None
        )
    except (TypeError, ValueError, OverflowError):
        pass

    return {
        "sender": str(sender),
        "recipient": str(recipient),
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "message_date": message_date,
        "attachments": attachments,
    }
