from app.services.email_parser import parse_email


def test_parse_plain_text_email():

    raw = b"""\
From: john@example.com
To: 796da3e350d41503@invismail.test
Subject: Test Email
Content-Type: text/plain; charset=utf-8

Hello from InvisMail!
"""

    result = parse_email(raw)

    assert result["sender"] == "john@example.com"
    assert result["recipient"] == "796da3e350d41503@invismail.test"
    assert result["subject"] == "Test Email"
    assert "Hello from InvisMail" in result["text_body"]