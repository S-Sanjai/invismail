
import asyncio
import logging
import os
import socket
import time

from redis.exceptions import ResponseError

from app.core.redis import redis_client
from app.services.email_processor import process_email_event


# --------------------------------------------------
# Configuration
# --------------------------------------------------

STREAM = "email:incoming"
GROUP = "email-workers"

CONSUMER = f"{socket.gethostname()}-{os.getpid()}"

DLQ = "email:dead-letter-queue"

MAX_ATTEMPTS = 5

SPOOL_DLQ_RETENTION_SECONDS = int(
    os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
)
SPOOL_ORPHAN_GRACE_SECONDS = int(
    os.getenv("SPOOL_ORPHAN_GRACE_SECONDS", "3600")
)

ATTEMPT_KEY_PREFIX = "email:attempts:"
LAST_ATTEMPT_PREFIX = "email:last-attempt:"
LAST_ERROR_PREFIX = "email:last-error:"

RETRY_INTERVAL = 60
LONG_RETRY_INTERVAL = 300

ATTEMPT_TTL = 7 * 24 * 60 * 60

RECOVERY_IDLE_TIME = 60_000  # 60 seconds
BATCH_SIZE = 5

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def as_text(value):
    """
    Convert Redis bytes or other values into strings.

    Supports Redis clients configured with either
    decode_responses=True or decode_responses=False.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if value is None:
        return ""

    return str(value)


def normalize_event_data(event_data):
    """
    Normalize Redis event keys and values into strings.
    """
    return {
        as_text(key): as_text(value)
        for key, value in event_data.items()
    }


def attempt_key(event_id):
    return f"{ATTEMPT_KEY_PREFIX}{as_text(event_id)}"


def last_attempt_key(event_id):
    return f"{LAST_ATTEMPT_PREFIX}{as_text(event_id)}"


def last_error_key(event_id):
    return f"{LAST_ERROR_PREFIX}{as_text(event_id)}"


def get_retry_delay(previous_attempts):
    """
    Determine retry delay based on previous failed attempts.

    Attempts 1-2: 60 seconds
    Attempts 3-5: 300 seconds
    """
    if previous_attempts < 3:
        return RETRY_INTERVAL

    return LONG_RETRY_INTERVAL


# --------------------------------------------------
# Consumer group initialization
# --------------------------------------------------

async def initialize_consumer_group():

    try:
        await redis_client.xgroup_create(
            STREAM,
            GROUP,
            id="0",
            mkstream=True,
        )

        logger.info(
            "Consumer group '%s' created.",
            GROUP,
        )

    except ResponseError as e:

        if "BUSYGROUP" in str(e):
            logger.info(
                "Consumer group '%s' already exists.",
                GROUP,
            )
        else:
            raise


# --------------------------------------------------
# Retry tracking
# --------------------------------------------------

async def record_attempt(event_id, error):

    pipe = redis_client.pipeline(transaction=True)

    pipe.incr(attempt_key(event_id))
    pipe.expire(attempt_key(event_id), ATTEMPT_TTL)

    pipe.set(
        last_attempt_key(event_id),
        time.time(),
        ex=ATTEMPT_TTL,
    )

    pipe.set(
        last_error_key(event_id),
        str(error)[:2000],
        ex=ATTEMPT_TTL,
    )

    results = await pipe.execute()

    attempts = int(results[0])

    logger.warning(
        "Event %s failed. Attempt %s/%s.",
        event_id,
        attempts,
        MAX_ATTEMPTS,
    )

    return attempts


async def get_attempts(event_id):

    attempts = await redis_client.get(
        attempt_key(event_id)
    )

    return int(as_text(attempts) or "0")


async def is_retry_due(event_id):
    """
    Check whether a recovered event has waited long enough
    before being processed again.
    """

    attempts = await get_attempts(event_id)

    if attempts == 0:
        return True

    last_attempt = await redis_client.get(
        last_attempt_key(event_id)
    )

    if last_attempt is None:
        return True

    delay = get_retry_delay(attempts)

    elapsed = time.time() - float(as_text(last_attempt))

    return elapsed >= delay


async def clear_retry_metadata(event_id):

    await redis_client.delete(
        attempt_key(event_id),
        last_attempt_key(event_id),
        last_error_key(event_id),
    )


# --------------------------------------------------
# Dead Letter Queue
# --------------------------------------------------

def _dlq_retention_seconds():
    value = globals().get("SPOOL_DLQ_RETENTION_SECONDS", None)
    if value is None:
        value = os.getenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    return int(value)


DLQ_HANDOFF_SCRIPT = """
local stream = KEYS[1]
local group = KEYS[2]
local dlq = KEYS[3]
local event_id = ARGV[1]

local pending = redis.call('XPENDING', stream, group, event_id, event_id, 1)
if (not pending) or (#pending == 0) then
    return 0
end

redis.call('XADD', dlq, unpack(ARGV, 2))
redis.call('XACK', stream, group, event_id)
for i = 4, #KEYS do
    redis.call('DEL', KEYS[i])
end
return 1
"""


async def move_to_dead_letter(
    event_id,
    event_data,
    attempts,
    failure_reason,
):
    """
    Conditionally move a failed event to the DLQ and acknowledge
    the original event using an atomic Redis Lua script.
    """

    failed_at = time.time()
    retention = _dlq_retention_seconds()
    spool_expires_at = failed_at + retention

    dlq_data = {
        **normalize_event_data(event_data),

        "original_event_id": as_text(event_id),
        "original_stream": STREAM,
        "failure_reason": str(failure_reason)[:2000],
        "attempts": str(attempts),
        "failed_at": str(failed_at),
        "spool_expires_at": str(spool_expires_at),
    }

    # Flatten DLQ data into ARGV pairs, prefixed by '*' for XADD id.
    argv = [as_text(event_id), "*"]
    for key, value in dlq_data.items():
        argv.append(str(key))
        argv.append(str(value))

    keys = [
        STREAM,
        GROUP,
        DLQ,
        attempt_key(event_id),
        last_attempt_key(event_id),
        last_error_key(event_id),
    ]

    redis = redis_client
    eval_fn = getattr(redis, "eval", None)

    # Prefer Lua scripting for atomic conditional handoff.
    if callable(eval_fn):
        try:
            result = await eval_fn(DLQ_HANDOFF_SCRIPT, len(keys), *keys, *argv)
        except TypeError:
            # Fall back to pipeline for clients/mocks without eval support.
            result = None
            pipe = redis.pipeline(transaction=True)
            pipe.xadd(DLQ, dlq_data)
            pipe.xack(STREAM, GROUP, event_id)
            pipe.delete(
                attempt_key(event_id),
                last_attempt_key(event_id),
                last_error_key(event_id),
            )
            pipe_result = await pipe.execute()
            if not pipe_result or len(pipe_result) < 3:
                raise RuntimeError("DLQ handoff transaction failed")
            result = 1
        if result == 0:
            logger.info(
                "Event %s is no longer pending; DLQ record not created.",
                event_id,
            )
            return
        if result is None:
            raise RuntimeError("DLQ handoff script failed")

        logger.error(
            "Event %s moved to DLQ after %s attempts.",
            event_id,
            attempts,
        )
        return

    # No eval support: use transactional pipeline.
    pipe = redis.pipeline(transaction=True)
    pipe.xadd(DLQ, dlq_data)
    pipe.xack(STREAM, GROUP, event_id)
    pipe.delete(
        attempt_key(event_id),
        last_attempt_key(event_id),
        last_error_key(event_id),
    )
    pipe_result = await pipe.execute()
    if not pipe_result or len(pipe_result) < 3:
        raise RuntimeError("DLQ handoff transaction failed")

    logger.error(
        "Event %s moved to DLQ after %s attempts.",
        event_id,
        attempts,
    )


# --------------------------------------------------
# Event processing
# --------------------------------------------------

async def handle_event(
    event_id,
    event_data,
    recovered=False,
):

    event_id = as_text(event_id)
    event_data = normalize_event_data(event_data)

    attempts = await get_attempts(event_id)

    # If the maximum attempts have already been reached,
    # try moving the event to DLQ without processing again.
    if attempts >= MAX_ATTEMPTS:

        failure_reason = await redis_client.get(
            last_error_key(event_id)
        )

        await move_to_dead_letter(
            event_id,
            event_data,
            attempts,
            as_text(failure_reason) or "Maximum attempts exceeded",
        )

        return

    # Apply retry delay only to recovered pending events.
    if recovered:

        retry_due = await is_retry_due(event_id)

        if not retry_due:
            return

    try:

        logger.info(
            "Processing event %s.",
            event_id,
        )

        await process_email_event(
            event_id,
            event_data,
        )

        # Acknowledge only after successful processing.
        await redis_client.xack(
            STREAM,
            GROUP,
            event_id,
        )

        await clear_retry_metadata(event_id)

        logger.info(
            "Event %s processed and acknowledged.",
            event_id,
        )

    except ValueError as e:

    # Permanent processing failure.
    # Retrying an email for a deleted/expired inbox is pointless.
        logger.error(
            "Permanent failure for event %s: %s",
            event_id,
            e,
        )

        await move_to_dead_letter(
            event_id,
            event_data,
            attempts + 1,
            e,
        )

    except Exception as e:

        # Transient failure: retain the event for retry.
        logger.exception(
            "Temporary processing failure for event %s.",
            event_id,
        )

        attempts = await record_attempt(
            event_id,
            e,
        )

        if attempts >= MAX_ATTEMPTS:

            await move_to_dead_letter(
                event_id,
                event_data,
                attempts,
                e,
            )
        # Otherwise, leave the event pending.
        # It will be recovered after the idle threshold
        # and processed when its retry delay has elapsed.


# --------------------------------------------------
# Pending event recovery
# --------------------------------------------------

async def recover_pending_events():

    cursor = "0-0"

    while True:

        result = await redis_client.xautoclaim(
            name=STREAM,
            groupname=GROUP,
            consumername=CONSUMER,
            min_idle_time=RECOVERY_IDLE_TIME,
            start_id=cursor,
            count=BATCH_SIZE,
        )

        next_id, messages, deleted_ids = result

        for event_id, event_data in messages:

            await handle_event(
                event_id,
                event_data,
                recovered=True,
            )

        cursor = as_text(next_id)

        if cursor == "0-0":
            break


# --------------------------------------------------
# Main worker loop
# --------------------------------------------------

async def main():

    await initialize_consumer_group()

    logger.info(
        "Email worker started. Consumer: %s",
        CONSUMER,
    )

    while True:

        try:

            # First recover abandoned pending events.
            await recover_pending_events()

            # Then read new events.
            events = await redis_client.xreadgroup(
                GROUP,
                CONSUMER,
                {STREAM: ">"},
                count=BATCH_SIZE,
                block=5000,
            )

            if events:

                for stream_name, messages in events:

                    for event_id, event_data in messages:

                        await handle_event(
                            event_id,
                            event_data,
                        )

        except asyncio.CancelledError:
            logger.info("Email worker shutting down.")
            raise

        except Exception:

            logger.exception(
                "Email worker encountered an error."
            )

            await asyncio.sleep(5)


if __name__ == "__main__":

    from app.core.logging import setup_logging

    setup_logging("invismail.worker")

    asyncio.run(main())
