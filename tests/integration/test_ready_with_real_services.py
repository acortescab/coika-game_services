import httpx
import pytest
import redis.asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from coika_game_service.api.core.config import Settings
from coika_game_service.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def real_settings(monkeypatch):
    settings = Settings()
    if not settings.DATABASE_URL_WRITER.startswith("postgresql"):
        pytest.skip("COIKA_DATABASE_URL_WRITER no configurada")

    try:
        engine = create_async_engine(settings.DATABASE_URL_WRITER)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        client = redis.asyncio.from_url(settings.REDIS_URL)
        await client.ping()
        await client.aclose()
    except Exception as exc:
        pytest.skip(f"PostgreSQL/Redis no disponibles: {exc!r}")

    from coika_game_service import main

    monkeypatch.setattr(main, "settings", settings)
    return settings


async def test_ready_is_ok_with_real_postgres_and_redis(real_settings):
    app = create_app()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200, response.text
    assert response.json()["checks"] == {"db_reader": "ok", "db_writer": "ok", "redis": "ok"}
