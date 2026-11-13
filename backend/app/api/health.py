from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.redis import redis_client

router = APIRouter(tags=["Health"])


@router.get("/health/live")
async def liveness():
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness():
    checks = {
        "database": False,
        "redis": False,
    }

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass

    try:
        await redis_client.ping()
        checks["redis"] = True
    except Exception:
        pass

    ready = all(checks.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )