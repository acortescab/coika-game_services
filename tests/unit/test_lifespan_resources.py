from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.core.dependencies import (
    get_reader_session,
    get_redis,
    get_writer_session,
)
from coika_game_service.main import create_app, lifespan

pytestmark = pytest.mark.usefixtures("lifespan_settings")


def _request_for(app: FastAPI) -> SimpleNamespace:
    return SimpleNamespace(app=app)


async def test_startup_creates_writer_and_reader_engines_from_settings(
    lifespan_spies, lifespan_settings
):
    async with lifespan(FastAPI()):
        assert lifespan_spies.engine_urls == [
            lifespan_settings.DATABASE_URL_WRITER,
            lifespan_settings.DATABASE_URL_READER,
        ]


async def test_startup_creates_redis_client_from_settings(lifespan_spies, lifespan_settings):
    async with lifespan(FastAPI()):
        assert lifespan_spies.redis_urls == [lifespan_settings.REDIS_URL]


async def test_dependencies_resolve_resources_created_by_lifespan(lifespan_spies):
    app = FastAPI()
    async with lifespan(app):
        request = _request_for(app)

        assert get_redis(request) is lifespan_spies.redis

        for dependency in (get_writer_session, get_reader_session):
            generator = dependency(request)
            session = await anext(generator)
            assert isinstance(session, AsyncSession)
            await generator.aclose()


async def test_writer_and_reader_sessions_use_different_engines(lifespan_spies):
    app = FastAPI()
    async with lifespan(app):
        request = _request_for(app)
        writer_gen = get_writer_session(request)
        reader_gen = get_reader_session(request)
        writer = await anext(writer_gen)
        reader = await anext(reader_gen)

        assert writer.bind is not reader.bind
        assert "writer-host" in str(writer.bind.url)
        assert "reader-host" in str(reader.bind.url)

        await writer_gen.aclose()
        await reader_gen.aclose()


async def test_session_dependency_closes_session_after_request(lifespan_spies):
    app = FastAPI()
    async with lifespan(app):
        generator = get_writer_session(_request_for(app))
        session = await anext(generator)
        close_calls = []
        original_close = session.close

        async def tracking_close():
            close_calls.append(True)
            await original_close()

        session.close = tracking_close
        await generator.aclose()

        assert close_calls


async def test_shutdown_disposes_both_engines_and_closes_redis(lifespan_spies):
    async with lifespan(FastAPI()):
        assert lifespan_spies.disposed == 0
        assert lifespan_spies.redis.closed is False

    assert lifespan_spies.disposed == 2
    assert lifespan_spies.redis.closed is True


async def test_shutdown_releases_resources_even_if_app_raises(lifespan_spies):
    with pytest.raises(RuntimeError):
        async with lifespan(FastAPI()):
            raise RuntimeError("boom")

    assert lifespan_spies.disposed == 2
    assert lifespan_spies.redis.closed is True


async def test_create_app_wires_the_lifespan(lifespan_spies):
    """`create_app()` debe registrar el lifespan: al arrancar la app se crean los recursos."""
    app = create_app()

    async with app.router.lifespan_context(app):
        assert get_redis(_request_for(app)) is lifespan_spies.redis
        assert len(lifespan_spies.engine_urls) == 2

    assert lifespan_spies.disposed == 2
    assert lifespan_spies.redis.closed is True


def test_create_app_registers_health_routes():
    paths = set(create_app().openapi()["paths"])
    assert {"/health", "/health/ready"} <= paths
