from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EmailListItem(BaseModel):
    id: UUID
    sender: str
    subject: str
    received_at: datetime
    is_read: bool


class AttachmentResponse(BaseModel):
    id: UUID
    filename: str
    content_type: str
    size: int


class EmailDetail(BaseModel):
    id: UUID
    sender: str
    recipient: str
    subject: str
    text_body: str | None
    html_body: str | None
    received_at: datetime
    is_read: bool
    attachments: list[AttachmentResponse]