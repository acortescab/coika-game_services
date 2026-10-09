from contextlib import asynccontextmanager

import httpx
import redis.asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.core.config import settings
from coika_game_service.api.core.jwks import JWKSCache, JWKSUnavailable
from coika_game_service.api.core.security import InvalidTokenError
from coika_game_service.api.routes import health, matches, scores
from coika_game_service.api.services.match_service import GameModeNotFound, IdempotencyKeyReused
from coika_game_service.api.services.score_service import (
    MatchNotFound,
    MatchNotOpen,
    ScoreAlreadyExists,
)


def create_app() -> FastAPI:
    """
        Create and configure the FastAPI application.
    
        Returns:
            FastAPI: Configured FastAPI application instance.
        """
    
    app = FastAPI(title="Coika Game Services", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(matches.router)
    app.include_router(scores.router)

    @app.exception_handler(InvalidTokenError)
    async def invalid_token_handler(request, exc):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid token"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    @app.exception_handler(GameModeNotFound)
    async def game_mode_not_found_handler(request, exc):
        # The mode is a reference inside the body, so a wrong id is an invalid body (422)
        return JSONResponse(
            status_code=422,
            content={"detail": "Game mode not found"}
        )

    @app.exception_handler(IdempotencyKeyReused)
    async def idempotency_key_reused_handler(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": "Idempotency-Key already used for a different game mode"},
        )

    @app.exception_handler(JWKSUnavailable)
    async def jwks_unavailable_handler(request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service unavailable"},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(ScoreAlreadyExists)
    async def score_already_exists(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": "Score already exists"}
        )

    @app.exception_handler(MatchNotOpen)
    async def score_with_non_open_match(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": "Match not opened"}
        )   

    @app.exception_handler(MatchNotFound)
    async def score_with_match_not_found(request, exc):
        return JSONResponse(
            status_code=404,
            content={"detail": "Match not found"}
        )
    
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
    app.state.http = httpx.AsyncClient(timeout=5.0)
    app.state.jwks = JWKSCache(
        settings.AUTH_JWKS_URL, 
        settings.JWKS_CACHE_TTL_SECONDS, 
        app.state.http)
    try:
        yield
    finally:
        await engine_writer.dispose()
        await engine_reader.dispose()
        await app.state.redis.aclose()
        await app.state.http.aclose()

app = create_app()


def run() -> None:
    """Run the FastAPI application."""
    uvicorn.run("coika_game_service.main:app", host="0.0.0.0", port=8001, reload=True)
