import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.db.models import GameMode, Match


@pytest.fixture
async def sessions():
    """Session factory on the real database, skipped when it is not available."""
    settings = Settings()
    if not settings.DATABASE_URL_WRITER.startswith("postgresql"):
        pytest.skip("COIKA_DATABASE_URL_WRITER no configurada")
    engine = create_async_engine(settings.DATABASE_URL_WRITER)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            known = await session.get(GameMode, CLASSIC_GAME_MODE_ID)
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL no disponible: {exc!r}")
    if known is None:
        await engine.dispose()
        pytest.skip("El modo classic no está sembrado: ejecuta las migraciones")

    yield factory
    await engine.dispose()


@pytest.fixture
async def player(sessions):
    """A brand new player; its matches are removed afterwards."""
    player_id = uuid.uuid4()
    yield player_id
    async with sessions() as session:
        await session.execute(delete(Match).where(Match.player_id == player_id))
        await session.commit()


@pytest.fixture
async def other_mode(sessions):
    """A second game mode, removed afterwards together with its matches."""
    mode = GameMode(id=uuid.uuid4(), game_mode=f"test-{uuid.uuid4().hex[:8]}")
    async with sessions() as session:
        session.add(mode)
        await session.commit()
    yield mode.id
    async with sessions() as session:
        await session.execute(delete(Match).where(Match.game_mode_id == mode.id))
        await session.execute(delete(GameMode).where(GameMode.id == mode.id))
        await session.commit()


async def matches_of(sessions, player_id) -> list[Match]:
    """All the matches of a player, read straight from the database."""
    async with sessions() as session:
        result = await session.execute(select(Match).where(Match.player_id == player_id))
        return list(result.scalars())
