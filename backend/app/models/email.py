import uuid

from datetime import datetime

from sqlalchemy import (
    Text,
    String,
    DateTime,
    Boolean,
    BigInteger,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID 
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

class Email(Base):
    __tablename__ = "emails"

    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            name = "uq_emails_source_event"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    inbox_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("inboxes.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
    )

    source_event_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    recipient: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    sender: Mapped[str] = mapped_column(
    String(320),
    nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    text_body: Mapped[str|None] = mapped_column(
        Text,
        nullable=True,
    )

    html_body: Mapped[str|None] = mapped_column(
        Text,
        nullable=True,
    )

    message_date: Mapped[datetime|None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    raw_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
