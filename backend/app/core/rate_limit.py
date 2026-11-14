import os
import time

from fastapi import HTTPException, Request

from app.core.redis import redis_client


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def enforce_rate_limit(
    request: Request,
    scope: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    key = f"rate:{scope}:{_client_key(request)}"
    try:
        current = await redis_client.incr(key)
        if current == 1:
            await redis_client.expire(key, window_seconds)
        if current > limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        # Fail open if Redis is unavailable.
        return
