import os
from redis.asyncio import Redis

redis_client = Redis.from_url(
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
    health_check_interval=30,  # Optional: Set a health check interval to ensure the connection is alive
)