from app.models.inbox import Inbox
from app.models.email import Email
from app.models.attachment import Attachment

# This ensures Alembic can discover your models.
__all__ = ["Inbox", "Email", "Attachment"]
