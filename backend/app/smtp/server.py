from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

import asyncio
import os
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiosmtpd.controller import Controller
from sqlalchemy import select



from app.core.database import SessionLocal
from app.models.inbox import Inbox
from app.core.redis import redis_client


"""
-This is a development SMTP adapter, not a production mail server.

-It accepts only generated, active inbox addresses.

-It stores raw messages before publishing their queue events.

-It does not parse MIME content yet.

-SMTP acceptance is returned only after Redis accepts the event.

-If Redis fails, the raw file may remain in the spool. A later recovery process should reconcile orphaned spool files.
"""


PROJECT_ROOT = Path(__file__).resolve().parents[3] # Adjusted to go up three levels to reach the project root
SPOOL_DIR = PROJECT_ROOT / "storage" / "spool" # Directory to store raw email files

MAX_EMAIL_SIZE = int (os.getenv("MAX_EMAIL_SIZE_BYTES", 10 * 1024 * 1024))  # Default to 10 MB if not set

def save_raw_email(message_id: str, raw_data: bytes) -> str:
    """Save the raw email data to a file in the spool directory."""
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)

    final_path = SPOOL_DIR / f"{message_id}.eml"
    temp_path = SPOOL_DIR / f"{message_id}.tmp"

    with open(temp_path, "wb") as file:
        file.write(raw_data)
        file.flush()  # Ensure all data is written to disk
        os.fsync(file.fileno())  # Force write to disk

    os.replace(temp_path, final_path)
    return str(final_path)

class InvisMailHandler:
    """Custom SMTP handler for InvisMail."""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """To handle the RCPT TO command and validate the recipient."""
        email_address = address.lower()

        async with SessionLocal() as db: # Use SessionLocal to create a new async session
            result = await db.execute(
                select(Inbox).where(
                    Inbox.email_address == email_address,
                    Inbox.status == "active",
                    Inbox.is_active.is_(True),
                    Inbox.expires_at > datetime.now(timezone.utc)
                )
            )

            inbox = result.scalar_one_or_none() #This will return None if no matching inbox is found

        if inbox is None: # If no matching inbox is found, return a 550 error code
            return "550 Recipient does not exist or inbox is inactive/expired."

        envelope.rcpt_tos.append(inbox.email_address) # Add the valid recipient to the envelope

        return "250 Recipient successfully accepted for delivery."

        
    async def handle_DATA(self, server, session, envelope):

        raw_data = envelope.original_content

        if len(raw_data) > MAX_EMAIL_SIZE:
            return "552 Message size exceeds maximum allowed size."

        if not envelope.rcpt_tos:
            return "550 No valid recipients found."

        message_id = str(uuid.uuid4())

        # Use a Redis transaction so all recipient events are submitted
        # together rather than publishing them one at a time.
        pipeline = redis_client.pipeline(transaction=True)

        try:
            for recipient in envelope.rcpt_tos:

                # Each recipient gets an independent spool file.
                spool_id = str(uuid.uuid4())

                spool_path = await asyncio.to_thread(
                    save_raw_email,
                    spool_id,
                    raw_data,
                )

                pipeline.xadd(
                    "email:incoming",
                    {
                        "message_id": message_id,
                        "recipient": recipient,
                        "sender": envelope.mail_from or "unknown",
                        "spool_path": spool_path,
                        "received_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )

            await pipeline.execute()

            return "250 Message accepted for processing."

        except Exception:
            # Do not delete spool files here. If Redis executed the
            # transaction but the client lost its response, events may
            # already reference those files.
            import logging
            logging.exception(
                "SMTP ingestion failed for message %s",
                message_id,
            )

            return "451 Temporary server error. Please try again later."


def start_smtp_server():

    """Start the SMTP server."""

    handler = InvisMailHandler()

    controller = Controller(handler, hostname="127.0.0.1", port=2525, data_size_limit=MAX_EMAIL_SIZE)

    controller.start()

    print(f"SMTP server listening on {controller.hostname}:{controller.port} with max email size {MAX_EMAIL_SIZE} bytes.")

    return controller  # Return the controller to allow for graceful shutdown if needed

if __name__ == "__main__":
    controller = start_smtp_server()

    try:
        import time
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Shutting down SMTP server...")
        controller.stop()