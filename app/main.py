from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db import init_db
from app.webhooks import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database tables on startup
    init_db()
    yield


app = FastAPI(
    title="Reclaim — Bounded Revenue-Recovery Decision Engine",
    description="Intelligent, bounded revenue recovery engine for failed Razorpay payments",
    version="1.0.0",
    lifespan=lifespan
)

# Mount webhook routes
app.include_router(webhook_router)


@app.get("/")
def root():
    return {
        "service": "Reclaim",
        "version": "1.0.0",
        "description": "Bounded Revenue-Recovery Decision Engine",
        "status": "operational"
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
