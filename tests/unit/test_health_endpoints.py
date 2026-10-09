import httpx
import pytest
from fastapi import FastAPI

from coika_game_service.api.db.dependendencies import (
    get_reader_session,
    get_redis,
    get_writer_session,
)
from coika_game_service.api.routes import health
from tests.conftest import FakeRedis, FakeSession


def build_app(
    *, writer: FakeSession | None = None, reader: FakeSession | None = None, redis=None
) -> FastAPI:
    """App mínima con solo el router de health y las dependencias sustituidas por fakes."""
    app = FastAPI()
    app.include_router(health.router)

    async def writer_dep():
        yield writer or FakeSession()

    async def reader_dep():
        yield reader or FakeSession()

    app.dependency_overrides[get_writer_session] = writer_dep
    app.dependency_overrides[get_reader_session] = reader_dep
    app.dependency_overrides[get_redis] = lambda: redis or FakeRedis()
    return app


async def get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_liveness_does_not_touch_dependencies():
    """/health es liveness: debe responder aunque DB y Redis estén caídos."""
    app = build_app(writer=FakeSession(fail=True), redis=FakeRedis(fail_ping=True))

    response = await get(app, "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_ok_when_everything_is_up():
    writer, reader, redis = FakeSession(), FakeSession(), FakeRedis()

    response = await get(build_app(writer=writer, reader=reader, redis=redis), "/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"db_reader": "ok", "db_writer": "ok", "redis": "ok"},
    }
    assert writer.executed == 1
    assert reader.executed == 1


@pytest.mark.parametrize(
    ("kwargs", "expected_checks"),
    [
        (
            {"reader": FakeSession(fail=True)},
            {"db_reader": "error", "db_writer": "ok", "redis": "ok"},
        ),
        (
            {"writer": FakeSession(fail=True)},
            {"db_reader": "ok", "db_writer": "error", "redis": "ok"},
        ),
        (
            {"redis": FakeRedis(fail_ping=True)},
            {"db_reader": "ok", "db_writer": "ok", "redis": "error"},
        ),
        (
            {
                "reader": FakeSession(fail=True),
                "writer": FakeSession(fail=True),
                "redis": FakeRedis(fail_ping=True),
            },
            {"db_reader": "error", "db_writer": "error", "redis": "error"},
        ),
    ],
    ids=["reader-down", "writer-down", "redis-down", "all-down"],
)
async def test_ready_returns_503_and_reports_which_dependency_failed(kwargs, expected_checks):
    response = await get(build_app(**kwargs), "/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "unhealthy", "checks": expected_checks}
