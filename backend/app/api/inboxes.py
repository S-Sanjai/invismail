import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.security import generate_email_address, generate_access_token, hash_token
from app.models import Inbox
from app.schemas.inbox import CreateInboxSchema

router = APIRouter(
    prefix="/api/v1/inboxes",
    tags=["Inboxes"],
)

@router.post("", status_code=201)
async def create_inbox(request: CreateInboxSchema, db: AsyncSession = Depends(get_db),):
    domain = os.getenv("DEFAULT_DOMAIN", "invismail.test")  # Default domain if not set in environment variables

    for attempt in range(3):
        email_address = generate_email_address(domain)
        access_token = generate_access_token()

        inbox = Inbox(
            email_address=email_address,
            access_token_hash=hash_token(access_token),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=request.ttl_seconds),
        )

        db.add(inbox)
        try:
            await db.commit()
            await db.refresh(inbox)

            return {
                "id": str(inbox.id),
                "email_address": inbox.email_address,
                "access_token": access_token,
                "expires_at": inbox.expires_at.isoformat(),
                "status": inbox.status,
            }
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=500, detail="Failed to create a unique inbox after multiple attempts.")

    raise HTTPException(status_code=500, detail="Failed to create inbox due to an unexpected error.")