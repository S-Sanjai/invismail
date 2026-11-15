"""Small, side-effect-free helpers for validating Mailgun inbound Routes payloads."""
import hashlib
import hmac
from email.utils import getaddresses
from urllib.parse import parse_qs


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
