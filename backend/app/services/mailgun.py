"""Small, side-effect-free helpers for validating Mailgun inbound Routes payloads."""
import hashlib
import hmac
from email.utils import getaddresses
from urllib.parse import parse_qs

from starlette.datastructures import UploadFile
from starlette.requests import Request


def verify_mailgun_signature(
    timestamp: str,
    token: str,
    signature: str,
    signing_key: str,
) -> bool:
    """Verify a Mailgun Routes HMAC-SHA256 signature."""
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12 or not token or not signature:
        return False
    expected = hmac.new(
        signing_key.encode("utf-8"),
        f"{timestamp}{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def parse_mailgun_form(raw_body: bytes) -> dict[str, str]:
    """Parse the URL-encoded MIME-route payload, preserving 8-bit MIME bytes."""
    try:
        encoded = raw_body.decode("ascii")
        parsed = parse_qs(
            encoded,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=2000,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Malformed Mailgun form payload") from exc

    result: dict[str, str] = {}
    for key, values in parsed.items():
        if len(values) != 1:
            raise ValueError(f"Unexpected repeated Mailgun field: {key}")
        result[key] = values[0]
    return result


def normalize_recipient(value: str) -> str:
    """Extract exactly one mailbox from Mailgun's envelope recipient value."""
    addresses = [
        address.strip().lower()
        for _display_name, address in getaddresses([value])
        if address and "@" in address
    ]
    if len(addresses) != 1:
        raise ValueError("Expected exactly one recipient address")
    address = addresses[0]
    if len(address) > 320 or any(ch.isspace() for ch in address):
        raise ValueError("Invalid recipient address")
    return address


async def parse_mailgun_multipart(
    content_type_header: str,
    raw_body: bytes,
    max_part_size: int,
) -> dict[str, str | bytes]:
    """Parse a bounded Mailgun multipart Route payload.

    Mailgun uses multipart/form-data when an incoming email has attachments.
    This parser intentionally retains only the fields needed by the raw-MIME
    ingestion adapter and rejects duplicate security or message fields.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", content_type_header.encode("latin-1"))],
        "client": ("127.0.0.1", 0),
        "server": ("localhost", 443),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    request = Request(scope, receive=receive)
    form = await request.form(
        max_files=50,
        max_fields=2000,
        max_part_size=max_part_size,
    )
    try:
        fields: dict[str, str | bytes] = {}
        for name in ("timestamp", "token", "signature", "recipient", "sender", "body-mime"):
            values = form.getlist(name)
            if len(values) > 1:
                raise ValueError(f"Unexpected repeated Mailgun field: {name}")
            if not values:
                continue
            value = values[0]
            if isinstance(value, UploadFile):
                value = await value.read(max_part_size + 1)
            if isinstance(value, bytes):
                fields[name] = value
            elif isinstance(value, str):
                fields[name] = value
            else:
                raise ValueError(f"Invalid Mailgun field type: {name}")
        return fields
    finally:
        await form.close()
