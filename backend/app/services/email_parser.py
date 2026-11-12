
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime


def parse_email(raw_data: bytes) -> dict:
    message = BytesParser(
        policy=policy.default
    ).parsebytes(raw_data)

    text_body = None
    html_body = None
    attachments = []

    for part in message.walk():

        if part.is_multipart():
            continue

        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()

        payload = part.get_payload(decode=True) or b""

        # Attachment or explicitly named file
        if disposition == "attachment" or filename:
            attachments.append({
                "filename": filename or "attachment.bin",
                "content_type": content_type,
                "content": payload,
            })
            continue

        # Ignore inline images and other binary resources
        if disposition == "inline" and not content_type.startswith("text/"):
            continue

        if content_type == "text/plain":
            decoded = part.get_content()

            if isinstance(decoded, str):
                text_body = decoded

        elif content_type == "text/html":
            decoded = part.get_content()

            if isinstance(decoded, str):
                html_body = decoded

    sender = message.get("From", "")
    recipient = message.get("To", "")
    subject = str(message.get("Subject", ""))

    message_date = None

    try:
        message_date = parsedate_to_datetime(
            message.get("Date")
        ) if message.get("Date") else None
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
