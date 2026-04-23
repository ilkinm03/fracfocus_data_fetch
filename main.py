import uvicorn
from fastapi import FastAPI
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.tasks.scheduler import lifespan
from app.api.v1.router import api_router

settings = get_settings()
setup_logging(settings.LOG_LEVEL)

app = FastAPI(
    title="FracFocus Data API",
    description="Hydraulic fracturing disclosure data with incremental monthly sync.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.API_HOST, port=settings.API_PORT, reload=False)
