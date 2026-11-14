from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # Adjusted to go up two levels to reach the project root
# Load environment variables from .env file
load_dotenv(ROOT / ".env")

from fastapi import FastAPI

from app.core.logging import setup_logging

setup_logging("invismail.api")

from app.api.inboxes import router as inbox_router
from app.api.messages import router as message_router
from app.api.events import router as events_router
from app.api.health import router as health_router


app = FastAPI(
    title="Invismail API",
    description="Self-hosted disposable email service",
    version="1.0.0",
)

app.include_router(health_router)
app.include_router(inbox_router)
app.include_router(message_router)
app.include_router(events_router)

@app.get("/health")
async def health():
    return {"status": "healthy",
            "service": "Invismail API",
            "version": "1.0.0"}