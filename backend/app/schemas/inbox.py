from pydantic import BaseModel, Field

class CreateInboxSchema(BaseModel):
    # The time-to-live (TTL) for the inbox in seconds. Must be between 300 seconds (5 minutes) and 86400 seconds (24 hours).
    ttl_seconds: int = Field(
        default = 3600, 
        ge = 300,
        le = 86400,
    )