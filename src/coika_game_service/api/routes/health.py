from typing import Annotated, Any

import redis.asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.core.dependencies import (
    get_reader_session,
    get_redis,
    get_writer_session,
)

router = APIRouter(prefix="/health", tags=["health"])

WriteDBDep = Annotated[AsyncSession, Depends(get_writer_session)]
ReadDBDep = Annotated[AsyncSession, Depends(get_reader_session)]
RedisDep = Annotated[redis.asyncio.Redis, Depends(get_redis)]

@router.get("")
async def health() -> dict[str, str]:
    """
    Check if the service is healthy.

    Returns:
        dict[str, str]: Health status.
    """
    return {"status": "ok"}

@router.get("/ready")
async def health_redis(
    db_reader: ReadDBDep,
    db_writer: WriteDBDep,
    redis: RedisDep) -> dict[str, Any]:
    """
    Check if the service, databases and redis are healthy.

    Returns:
        dict[str, Any]: Health status and the result of each check.
    """
    checks = {}

    try:
        await db_reader.execute(text("SELECT 1"))
        checks["db_reader"] = "ok"
    except Exception:
        checks["db_reader"] = "error"
    try:
        await db_writer.execute(text("SELECT 1"))
        checks["db_writer"] = "ok"
    except Exception:
        checks["db_writer"] = "error"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    if any(status != "ok" for status in checks.values()):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "checks": checks,
            },
        )

    return {
        "status": "ok",
        "checks": checks,
    }
