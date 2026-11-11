from fastapi import FastAPI
from app.api.inboxes import router as inbox_router

from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # Adjusted to go up two levels to reach the project root
# Load environment variables from .env file
load_dotenv(ROOT / ".env")

app = FastAPI(
    title="Invismail API",
    description="Self-hosted disposable email service",
    version="1.0.0",
)

app.include_router(inbox_router)

@app.get("/health")
async def health():
    return {"status": "healthy",
            "service": "Invismail API",
            "version": "1.0.0"}