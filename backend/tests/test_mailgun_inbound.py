import hashlib
import hmac
from urllib.parse import quote_from_bytes

import pytest

from app.api.inbound import (
    normalize_recipient,
    parse_mailgun_form,
    verify_mailgun_signature,
)


def test_mailgun_signature_accepts_valid_signature():
    timestamp = "1770920772"
    token = "test-token-value"
    signing_key = "mailgun-webhook-key"
    signature = hmac.new(
        signing_key.encode(),
        f"{timestamp}{token}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert verify_mailgun_signature(timestamp, token, signature, signing_key)


def test_mailgun_signature_rejects_tampered_signature():
    assert not verify_mailgun_signature(
        "1770920772",
        "test-token-value",
        "0" * 64,
        "mailgun-webhook-key",
    )


def test_mailgun_signature_rejects_missing_or_invalid_fields():
    assert not verify_mailgun_signature("", "token", "signature", "key")
    assert not verify_mailgun_signature("abc", "token", "signature", "key")
    assert not verify_mailgun_signature("123", "", "signature", "key")


def test_parse_mailgun_form_preserves_raw_mime_bytes():
    raw_mime = (
        b"From: sender@example.com\r\n"
        b"To: inbox@testingtemp.publicvm.com\r\n"
        b"Subject: Raw MIME test\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello + InvisMail = caf\xe9\r\n"
    )
    body = (
        "timestamp=1770920772&token=abc&signature=def&"
        "recipient=inbox%40testingtemp.publicvm.com&body-mime="
        + quote_from_bytes(raw_mime)
    ).encode("ascii")

    fields = parse_mailgun_form(body)

    assert fields["recipient"] == "inbox@testingtemp.publicvm.com"
    assert fields["body-mime"].encode("utf-8", errors="surrogateescape") == raw_mime


def test_parse_mailgun_form_rejects_repeated_fields():
    with pytest.raises(ValueError, match="repeated"):
        parse_mailgun_form(b"timestamp=1&token=a&token=b")


def test_normalize_recipient():
    assert normalize_recipient("Inbox <USER@testingtemp.publicvm.com>") == (
        "user@testingtemp.publicvm.com"
    )


def test_normalize_recipient_rejects_multiple_addresses():
    with pytest.raises(ValueError):
        normalize_recipient("one@example.com, two@example.com")
