import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.core.jwks import JWKSCache
from coika_game_service.api.db.models import GameMode, GameModeName, Match, Score
from coika_game_service.api.services import score_service
from coika_game_service.main import create_app
from tests.auth_helpers import make_jwks, make_keypair, make_token


@pytest.fixture
def keypair():
    return make_keypair()


class NullPipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    async def execute(self):
        # The shape of the rate limit pipeline (count, expire, ttl): one hit, far from the limit
        return [1, True, 60]


class NullRedis:
    """
    A Redis that stores nothing. The HTTP tests send real scores, and with the real Redis of the
    docker stack they would leave fake players in its rankings.
    """

    def pipeline(self, transaction=True):
        return NullPipeline()

    async def zadd(self, *args, **kwargs):
        return 0

    async def zrevrange(self, *args, **kwargs):
        return []

    async def mget(self, keys):
        return [None] * len(keys)

    async def aclose(self):
        return None


@pytest.fixture
async def client(sessions, keypair):
    """
    The real app (lifespan, routes, services, Postgres) called over HTTP. Only the auth
    service is simulated: its JWKS is served from memory and the tokens are really signed.
    Redis is replaced by one that stores nothing, so the tests leave no trace in the rankings.
    """
    app = create_app()
    async with app.router.lifespan_context(app):
        await app.state.redis.aclose()
        app.state.redis = NullRedis()
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
    """
    A second game mode besides classic and daily: zen. The catalog only allows the names of
    GameModeName (a CHECK constraint), so a throwaway mode cannot be created. The matches of
    the tests belong to the `player` fixtures, which remove them.
    """
    async with sessions() as session:
        return await session.scalar(
            select(GameMode.id).where(GameMode.game_mode == GameModeName.ZEN)
        )


class RecordingCache:
    """Stands in for Redis in the service tests: records what would be written to the ranking."""

    def __init__(self):
        self.calls = []

    async def update_max_score(self, player_id, game_mode, score):
        self.calls.append(("classic", player_id, game_mode, score))

    async def update_max_score_daily(self, player_id, game_mode, score, seed):
        self.calls.append(("daily", player_id, game_mode, score, seed))

    async def hit_rate_limit(self, player_id, prefix):
        return False, 50, 120


async def age_match(sessions, match_id, seconds=60) -> None:
    """
    Makes a match look like it started `seconds` ago. The anti-cheat rules (HU-06) measure the
    duration from started_at, and a test cannot wait a real minute before sending its score.
    """
    async with sessions() as session:
        await session.execute(
            update(Match)
            .where(Match.id == uuid.UUID(str(match_id)))
            .values(started_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )
        await session.commit()


@pytest.fixture
def freeze_service_clock(monkeypatch):
    """
    Returns a function that sets the 'now' the score service sees, for matches whose
    started_at is fixed by the test (so it cannot be aged).
    """

    def freeze(moment: datetime) -> None:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment

        monkeypatch.setattr(score_service, "datetime", FrozenDatetime)

    return freeze


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
