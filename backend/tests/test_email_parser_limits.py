import pytest

from app.services.email_parser import parse_email


def test_plain_email_still_parses():
    raw = b"""From: a@example.com
To: b@invismail.test
Subject: Hi
Content-Type: text/plain; charset=utf-8

Hello
"""
    result = parse_email(raw)
    assert result["text_body"].strip() == "Hello"


def test_too_many_mime_parts_raises():
    # Build a deeply nested multipart with many parts.
    parts = []
    for i in range(105):
        parts.append(
            f"Content-Type: text/plain; charset=utf-8\r\n\r\npart {i}\r\n"
        )
    boundary = "BOUNDARY"
    body = (
        "MIME-Version: 1.0\r\n"
        f"Content-Type: multipart/mixed; boundary={boundary}\r\n"
        "\r\n"
    )
    for p in parts:
        body += f"--{boundary}\r\n{p}"
    body += f"--{boundary}--\r\n"
    with pytest.raises(ValueError):
        parse_email(body.encode())


def test_attachment_total_limit_raises(monkeypatch):
    import app.services.email_parser as parser

    monkeypatch.setattr(parser, "MAX_TOTAL_ATTACHMENT_BYTES", 1)
    raw = b"""From: a@example.com
To: b@invismail.test
Subject: Hi
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=abc

--abc
Content-Type: text/plain

hello
--abc
Content-Disposition: attachment; filename="a.txt"
Content-Type: application/octet-stream

AAAA
--abc--
"""
    with pytest.raises(ValueError):
        parse_email(raw)
