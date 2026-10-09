import uuid

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.core.jwks import JWKSCache
from coika_game_service.api.db.models import GameMode, Match, Score
from coika_game_service.main import create_app
from tests.auth_helpers import make_jwks, make_keypair, make_token


@pytest.fixture
def keypair():
    return make_keypair()


@pytest.fixture
async def client(sessions, keypair):
    """
    The real app (lifespan, routes, services, Postgres) called over HTTP. Only the auth
    service is simulated: its JWKS is served from memory and the tokens are really signed.
    """
    app = create_app()
    async with app.router.lifespan_context(app):
        mock_auth = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=make_jwks(keypair)))
        )
        app.state.jwks = JWKSCache("http://auth/jwks", 300, mock_auth)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def headers(keypair, player_id, key=None):
    """Request headers: a really signed token for the player and, optionally, the match key."""
    result = {"Authorization": f"Bearer {make_token(keypair, sub=str(player_id))}"}
    if key is not None:
        result["Idempotency-Key"] = str(key)
    return result


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


async def purge_player(sessions, player_id) -> None:
    """Deletes the scores and matches of a player (scores first: they reference matches)."""
    async with sessions() as session:
        match_ids = select(Match.id).where(Match.player_id == player_id)
        await session.execute(delete(Score).where(Score.match_id.in_(match_ids)))
        await session.execute(delete(Match).where(Match.player_id == player_id))
        await session.commit()


@pytest.fixture
async def player(sessions):
    """A brand new player; its scores and matches are removed afterwards."""
    player_id = uuid.uuid4()
    yield player_id
    await purge_player(sessions, player_id)


@pytest.fixture
async def other_player(sessions):
    """A second brand new player, cleaned up like `player`."""
    player_id = uuid.uuid4()
    yield player_id
    await purge_player(sessions, player_id)


@pytest.fixture
async def other_mode(sessions):
    """A second game mode, removed afterwards together with its matches."""
    mode = GameMode(id=uuid.uuid4(), game_mode=f"test-{uuid.uuid4().hex[:8]}")
    async with sessions() as session:
        session.add(mode)
        await session.commit()
    yield mode.id
    async with sessions() as session:
        match_ids = select(Match.id).where(Match.game_mode_id == mode.id)
        await session.execute(delete(Score).where(Score.match_id.in_(match_ids)))
        await session.execute(delete(Match).where(Match.game_mode_id == mode.id))
        await session.execute(delete(GameMode).where(GameMode.id == mode.id))
        await session.commit()


async def scores_of(sessions, player_id) -> list[Score]:
    """All the scores of a player's matches, read straight from the database."""
    async with sessions() as session:
        match_ids = select(Match.id).where(Match.player_id == player_id)
        result = await session.execute(select(Score).where(Score.match_id.in_(match_ids)))
        return list(result.scalars())


async def matches_of(sessions, player_id) -> list[Match]:
    """All the matches of a player, read straight from the database."""
    async with sessions() as session:
        result = await session.execute(select(Match).where(Match.player_id == player_id))
        return list(result.scalars())
