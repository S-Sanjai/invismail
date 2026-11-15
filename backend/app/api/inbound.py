"""Authenticated Mailgun inbound-route integration.

Mailgun Routes configured with a destination ending in mime POST an
application/x-www-form-urlencoded body containing the original message in
the body-mime field. This adapter validates Mailgun's signature and feeds
the existing Redis email:incoming stream.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.redis import redis_client
from app.models.inbox import Inbox
from app.services.storage_service import SPOOL_DIR, safe_delete_spool_file

logger = logging.getLogger("invismail.mailgun")
router = APIRouter(prefix="/api/v1/inbound", tags=["Inbound email"])

MAX_EMAIL_SIZE = int(os.getenv("MAX_EMAIL_SIZE_BYTES", 10 * 1024 * 1024))
MAX_WEBHOOK_REQUEST_BYTES = int(
    os.getenv("MAILGUN_MAX_WEBHOOK_BYTES", str(MAX_EMAIL_SIZE * 3 + 65536))
)
MAX_SIGNATURE_AGE_SECONDS = int(
    os.getenv("MAILGUN_WEBHOOK_MAX_AGE_SECONDS", str(24 * 60 * 60))
)
LOCK_SECONDS = int(os.getenv("MAILGUN_WEBHOOK_LOCK_SECONDS", "180"))
DEDUP_SECONDS = int(os.getenv("MAILGUN_WEBHOOK_DEDUP_SECONDS", str(7 * 24 * 60 * 60)))

# Queueing the Redis event and marking the provider token complete are atomic.
QUEUE_AND_MARK_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return ''
end
local event_id = redis.call(
    'XADD', KEYS[2], '*',
    'message_id', ARGV[2],
    'recipient', ARGV[3],
    'sender', ARGV[4],
    'spool_path', ARGV[5],
    'received_at', ARGV[6]
)
redis.call('SET', KEYS[1], 'done', 'EX', ARGV[7])
return event_id
"""

RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def verify_mailgun_signature(
    timestamp: str,
    token: str,
    signature: str,
    signing_key: str,
) -> bool:
    """Verify a Mailgun Routes HMAC-SHA256 signature."""
    if not timestamp.isdigit() or not token or not signature:
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


def save_raw_email(message_id: str, raw_data: bytes) -> str:
    """Atomically save raw MIME under a generated filename in the spool directory."""
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    final_path = SPOOL_DIR / f"{message_id}.eml"
    temp_path = SPOOL_DIR / f"{message_id}.tmp"
    with open(temp_path, "xb") as file:
        file.write(raw_data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, final_path)
    return str(final_path)


async def _read_bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_REQUEST_BYTES:
                raise HTTPException(status_code=406, detail="Webhook payload exceeds size limit")
        except ValueError:
            raise HTTPException(status_code=406, detail="Invalid Content-Length")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_REQUEST_BYTES:
            raise HTTPException(status_code=406, detail="Webhook payload exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/mailgun/raw-mime")
async def receive_mailgun_raw_mime(request: Request):
    """Receive one raw MIME email forwarded by a signed Mailgun Route."""
    signing_key = os.getenv("MAILGUN_WEBHOOK_SIGNING_KEY", "").strip()
    if not signing_key:
        logger.error("Mailgun webhook received but signing key is not configured")
        raise HTTPException(status_code=503, detail="Inbound email provider is not configured")

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=406, detail="Expected URL-encoded Mailgun MIME payload")

    raw_body = await _read_bounded_body(request)
    try:
        fields = parse_mailgun_form(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=406, detail=str(exc)) from exc

    timestamp = fields.get("timestamp", "")
    token = fields.get("token", "")
    signature = fields.get("signature", "")
    if not verify_mailgun_signature(timestamp, token, signature, signing_key):
        raise HTTPException(status_code=406, detail="Invalid Mailgun signature")

    now = int(datetime.now(timezone.utc).timestamp())
    signed_at = int(timestamp)
    if signed_at > now + 300 or now - signed_at > MAX_SIGNATURE_AGE_SECONDS:
        raise HTTPException(status_code=406, detail="Expired Mailgun webhook signature")

    try:
        recipient = normalize_recipient(fields.get("recipient", ""))
    except ValueError as exc:
        raise HTTPException(status_code=406, detail=str(exc)) from exc

    mime_text = fields.get("body-mime")
    if not mime_text:
        raise HTTPException(status_code=406, detail="Mailgun payload does not contain body-mime")
    mime_bytes = mime_text.encode("utf-8", errors="surrogateescape")
    if not mime_bytes or len(mime_bytes) > MAX_EMAIL_SIZE:
        raise HTTPException(status_code=406, detail="Email exceeds configured size limit")

    token_digest = hashlib.sha256(f"{timestamp}:{token}".encode("utf-8")).hexdigest()
    dedup_key = f"mailgun:inbound:dedup:{token_digest}"
    lock_value = uuid.uuid4().hex
    acquired = await redis_client.set(dedup_key, lock_value, nx=True, ex=LOCK_SECONDS)
    if not acquired:
        current = await redis_client.get(dedup_key)
        if current == "done":
            return {"status": "duplicate"}
        raise HTTPException(status_code=503, detail="This Mailgun delivery is already being processed")

    spool_path: str | None = None
    queue_attempted = False
    try:
        async with SessionLocal() as db:
            result = await db.execute(
                select(Inbox).where(
                    Inbox.email_address == recipient,
                    Inbox.status == "active",
                    Inbox.is_active.is_(True),
                    Inbox.expires_at > datetime.now(timezone.utc),
                )
            )
            inbox = result.scalar_one_or_none()

        if inbox is None:
            raise HTTPException(status_code=406, detail="Recipient does not exist or inbox is inactive/expired")

        message_id = str(uuid.uuid4())
        spool_path = await asyncio.to_thread(save_raw_email, message_id, mime_bytes)
        sender = (fields.get("sender") or "unknown")[:320]
        received_at = datetime.now(timezone.utc).isoformat()

        queue_attempted = True
        event_id = await redis_client.eval(
            QUEUE_AND_MARK_SCRIPT,
            2,
            dedup_key,
            "email:incoming",
            lock_value,
            message_id,
            recipient,
            sender,
            spool_path,
            received_at,
            DEDUP_SECONDS,
        )
        if not event_id:
            await asyncio.to_thread(safe_delete_spool_file, spool_path, missing_ok=True)
            spool_path = None
            raise HTTPException(status_code=503, detail="Mailgun delivery lock expired before queueing")

        logger.info("Accepted Mailgun message %s for %s (Redis event %s)", message_id, recipient, event_id)
        return {"status": "accepted", "message_id": message_id}

    except HTTPException:
        # If EVAL was attempted, its outcome may be ambiguous (for example, the
        # Redis response was lost after execution). Preserve the spool file so
        # a committed stream event can never point at a deleted message.
        if spool_path and not queue_attempted:
            await asyncio.to_thread(safe_delete_spool_file, spool_path, missing_ok=True)
        try:
            await redis_client.eval(RELEASE_LOCK_SCRIPT, 1, dedup_key, lock_value)
        except Exception:
            logger.exception("Could not release Mailgun delivery lock")
        raise
    except Exception as exc:
        if spool_path and not queue_attempted:
            await asyncio.to_thread(safe_delete_spool_file, spool_path, missing_ok=True)
        try:
            await redis_client.eval(RELEASE_LOCK_SCRIPT, 1, dedup_key, lock_value)
        except Exception:
            logger.exception("Could not release Mailgun delivery lock")
        logger.exception("Temporary Mailgun ingestion failure")
        raise HTTPException(status_code=503, detail="Temporary inbound email processing failure") from exc
