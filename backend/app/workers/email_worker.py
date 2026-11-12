import asyncio
import os
import socket

import time
import logging


from redis.exceptions import ResponseError

from app.core.redis import redis_client
from app.services.email_processor import process_email_event

STREAM = "email:incoming"
GROUP = "email-workers"
CONSUMER = f"{socket.gethostname()}-{os.getpid()}"  # Unique identifier for this worker instance

MAX_ATTEMPTS=5
ATTEMPT_KEY_PREFIX = "email:attempts:"
LAST_ATTEMPT_PREFIX = "email:last-attempt:"
ATTEMPT_TTL = 1*60*60

RECOVERY_IDLE_TIME = 60_000
BATCH_SIZE = 5

RETRY_INTERVEL = 60
LONG_RETRY_INTERVEL = 300

DLQ = "email:dead-letter-queue"  # Dead Letter Queue for failed messages

logger = logging.getLogger(__name__)


async def initialize_consumer_group():
    try:
        await redis_client.xgroup_create(STREAM, GROUP, id='0', mkstream=True)
        print(f"Consumer group '{GROUP}' created for stream '{STREAM}'.")
    except ResponseError as e:
        if "BUSYGROUP" in str(e):
            print(f"Consumer group '{GROUP}' already exists.")
        else:
            raise

def attempt_key(event_id):
    return f"{ATTEMPT_KEY_PREFIX}{event_id}"

def last_attempt_key(event_id):
    return f"{LAST_ATTEMPT_PREFIX}{event_id}"

def get_retry_delay(previous_attempts):
    if previous_attempts >= 3:
        return LONG_RETRY_INTERVEL

    return RETRY_INTERVEL

async def is_retry_due(event_id):

    attemp


async def handle_event(event_id, event_data):

    try:
        await process_email_event(event_id, event_data)
        await redis_client.xack(STREAM, GROUP, event_id)
        print(f"Processed and acknowledged email event: {event_id}.")
    except Exception as e:
        print(f"Error processing email event {event_id}: {e}")
        # Move the failed message to the Dead Letter Queue
        # await redis_client.xadd(DLQ, event_data)
        # await redis_client.xack(STREAM, GROUP, event_id)
        # print(f"Moved email event {event_id} to Dead Letter Queue.")

async def recover_pending_events():

    result = await redis_client.xautoclaim(
        name=STREAM,
        groupname=GROUP,
        consumername=CONSUMER,
        min_idle_time=60000,
        start_id="0-0",
        count=5,
    )

    next_id, messages, deleted_ids = result

    for event_id, data in messages:
        await handle_event(event_id, data)


async def main():
    await initialize_consumer_group()

    while True:
        try:
            events = await redis_client.xreadgroup(GROUP, CONSUMER, {STREAM: '>'}, count=5, block=5000) # Wait for 5 seconds if no messages are available
            if events:
                for stream_name, messages in events:
                    for event_id, event_data in messages:
                        await handle_event(event_id, event_data) 
        except Exception as e:
            print(f"Error reading from stream: {e}")
            await asyncio.sleep(5)  # Wait before retrying on error

if __name__ == "__main__":
    asyncio.run(main())