from contextlib import asynccontextmanager

import redis.asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.core.config import settings
from coika_game_service.api.routes import health


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application instance.
    """
    app = FastAPI(title="Coika Game Services", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    return app

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Async context manager for handling application lifespan events.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None
    """
    engine_writer = create_async_engine(settings.DATABASE_URL_WRITER, pool_pre_ping=True)
    engine_reader = create_async_engine(settings.DATABASE_URL_READER, pool_pre_ping=True)
    app.state.session_writer = async_sessionmaker(engine_writer, expire_on_commit=False)
    app.state.session_reader = async_sessionmaker(engine_reader, expire_on_commit=False)
    app.state.redis = redis.asyncio.from_url(settings.REDIS_URL)
    try:
        yield
    finally:
        await engine_writer.dispose()
        await engine_reader.dispose()
        await app.state.redis.aclose()

app = create_app()


def run() -> None:
    """Run the FastAPI application."""
    uvicorn.run("coika_game_service.main:app", host="0.0.0.0", port=8001, reload=True)
