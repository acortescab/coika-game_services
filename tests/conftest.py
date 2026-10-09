from types import SimpleNamespace

import pytest


class FakeRedis:
    """
    Fake Redis client for testing.
    """
    def __init__(self, fail_ping: bool = False):
        self.fail_ping = fail_ping
        self.closed = False

    async def ping(self) -> bool:
        if self.fail_ping:
            raise ConnectionError("redis down")
        return True

    async def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class FakeSession:
    """
    Fake database session for testing.
    """
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.executed = 0

    async def execute(self, *args, **kwargs):
        self.executed += 1
        if self.fail:
            raise ConnectionError("db down")


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def lifespan_settings(monkeypatch):
    """
    Fixture that patches settings to use fake services.
    """
    from coika_game_service import main

    fake = SimpleNamespace(
        DATABASE_URL_WRITER="postgresql+asyncpg://u:p@writer-host:5432/db",
        DATABASE_URL_READER="postgresql+asyncpg://u:p@reader-host:5432/db",
        REDIS_URL="redis://redis-host:6379/0",
        AUTH_JWKS_URL="http://auth-host:8000/.well-known/jwks.json",
        JWKS_CACHE_TTL_SECONDS=300
    )
    monkeypatch.setattr(main, "settings", fake)
    return fake


@pytest.fixture
def lifespan_spies(monkeypatch, fake_redis):
    """
    Fixture that patches database and redis creation to capture arguments.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

    from coika_game_service import main

    spies = SimpleNamespace(engine_urls=[], disposed=0, redis_urls=[], redis=fake_redis)

    def spy_create_engine(url, *args, **kwargs):
        spies.engine_urls.append(url)
        return create_async_engine(url, *args, **kwargs)

    real_dispose = AsyncEngine.dispose

    async def spy_dispose(self, *args, **kwargs):
        spies.disposed += 1
        return await real_dispose(self, *args, **kwargs)

    def spy_from_url(url, *args, **kwargs):
        spies.redis_urls.append(url)
        return fake_redis

    monkeypatch.setattr(main, "create_async_engine", spy_create_engine)
    monkeypatch.setattr(AsyncEngine, "dispose", spy_dispose)
    monkeypatch.setattr("redis.asyncio.from_url", spy_from_url)
    return spies
