import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.auth import get_authenticated_inbox
from app.core.redis import redis_client
from app.models.inbox import Inbox

router = APIRouter(tags=["Events"])

@router.get("/api/v1/inboxes/me/events")
async def inbox_events(
    inbox: Inbox = Depends(get_authenticated_inbox)
):

    async def event_generator():
        pubsub = redis_client.pubsub()
        channel = f"inbox:{inbox.id}:events"

        await pubsub.subscribe(channel)

        try:
            yield "event: connected\ndata:{}\n\n"

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=15,
                )

                if message:
                    payload = {
                        "message_id": message["data"]
                    }

                    yield (
                        "event: new_email\n"
                        f"data: {json.dumps(payload)}\n\n"
                    )

                else:
                    yield ": heartbeat\n\n"

                await asyncio.sleep(0.1)

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )