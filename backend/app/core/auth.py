
import uuid

from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import hash_token
from app.models.inbox import Inbox


security = HTTPBearer()


async def get_authenticated_inbox(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    token = credentials.credentials
    token_hash = hash_token(token)

    result = await db.execute(
        select(Inbox).where(
            Inbox.access_token_hash == token_hash,
            Inbox.status == "active",
            Inbox.is_active.is_(True),
            Inbox.expires_at > datetime.now(timezone.utc),
        )
    )

    inbox = result.scalar_one_or_none()

    if inbox is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired inbox credentials",
        )

    return inbox
