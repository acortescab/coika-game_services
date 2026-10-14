import uuid
from datetime import UTC, datetime

import pytest
import redis.asyncio

from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.exceptions import InvalidScore, RebuildAlreadyRunning
from coika_game_service.api.core.game_modes import (
    CLASSIC_GAME_MODE_ID,
    DAILY_GAME_MODE_ID,
    daily_seed,
)
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.match_service import MatchService
from coika_game_service.api.services.score_service import ScoreService
from tests.integration.conftest import age_match, purge_player, redis_url_for_db

pytestmark = pytest.mark.integration

TEST_DB = 15


class NoRateLimitCache(RedisRepository):
    """The real Redis repository, without the rate limit: the tests play many matches."""

    async def hit_rate_limit(self, player_id, prefix):
        return False, 50, 120


@pytest.fixture
async def redis_db():
    """Redis client on a throwaway DB. FLUSHDB is only allowed after checking which DB it is."""
    conn = redis.asyncio.from_url(
        redis_url_for_db(Settings().REDIS_URL, TEST_DB), decode_responses=True)
    try:
        await conn.ping()
    except Exception as exc:
        pytest.skip(f"Redis no disponible: {exc!r}")

    # Safety guard: if from_url ignored db=15 we would wipe the real data.
    assert conn.connection_pool.connection_kwargs["db"] == TEST_DB

    await conn.flushdb()
    try:
        yield conn
    finally:
        await conn.flushdb()
        await conn.aclose()


@pytest.fixture
async def third_player(sessions):
    player_id = uuid.uuid4()
    yield player_id
    await purge_player(sessions, player_id)


async def play(sessions, cache, player_id, mode_id, score, finish=True):
    """
    One match like the API does it: create it, age it so the anti-cheat rules accept the
    figures, and send the score. Without `finish` the match is left open.
    """
    async with sessions() as session:
        matches = MatchService(
            MatchRepository(session, session), GameModeRepository(session, session),
            cache, session)
        match, _ = await matches.create_match(player_id, mode_id, uuid.uuid4())
    if not finish:
        return

    await age_match(sessions, match.id, 60)
    async with sessions() as session:
        scores = ScoreService(
            ScoreRepository(session, session), MatchRepository(session, session),
            cache, GameModeRepository(session, session), session)
        payload = CreateScoreRequest(score=score, pieces_dropped=10, highest_tier=1)
        await scores.create_score(player_id, match.id, payload)


async def rebuild(sessions, cache) -> int:
    """The rebuild-leaderboards command, without the CLI."""
    async with sessions() as session:
        scores = ScoreService(
            ScoreRepository(session, session), MatchRepository(session, session),
            cache, GameModeRepository(session, session), session)
        service = LeaderboardService(cache, None, scores)
        return await service.rebuild_ranking()


async def snapshot(redis_db, players) -> dict:
    """
    {ranking key: {player: score}} for the test players only: the rebuild reads the whole
    database, which may hold more players than the ones of this test.
    """
    wanted = {str(p) for p in players}
    result = {}
    async for key in redis_db.scan_iter(match="leaderboard:*"):
        if key.endswith(":tmp"):
            continue
        entries = await redis_db.zrange(key, 0, -1, withscores=True)
        mine = {member: int(score) for member, score in entries if member in wanted}
        if mine:
            result[key] = mine
    return result


@pytest.fixture
async def played(sessions, redis_db, player, other_player, third_player, other_mode):
    """
    A session of games for three players, played through the real services:
    a better and a worse score of the same player, several modes, two daily matches,
    an abandoned match, a match left open and a rejected one.
    """
    cache = NoRateLimitCache(redis_db)

    await play(sessions, cache, player, CLASSIC_GAME_MODE_ID, 1000)
    await play(sessions, cache, player, CLASSIC_GAME_MODE_ID, 3000)
    await play(sessions, cache, player, CLASSIC_GAME_MODE_ID, 2000)  # worse: must not lower it
    await play(sessions, cache, other_player, CLASSIC_GAME_MODE_ID, 1500)
    await play(sessions, cache, third_player, CLASSIC_GAME_MODE_ID, 2500)

    await play(sessions, cache, player, other_mode, 800)
    await play(sessions, cache, other_player, other_mode, 900)

    await play(sessions, cache, player, DAILY_GAME_MODE_ID, 1200)
    await play(sessions, cache, player, DAILY_GAME_MODE_ID, 1800)
    await play(sessions, cache, other_player, DAILY_GAME_MODE_ID, 2400)

    # Matches that must not reach any ranking
    await play(sessions, cache, third_player, CLASSIC_GAME_MODE_ID, 0, finish=False)  # abandoned
    await play(sessions, cache, third_player, CLASSIC_GAME_MODE_ID, 0, finish=False)  # open
    with pytest.raises(InvalidScore):  # 999999 in one minute exceeds the max score per second
        await play(sessions, cache, other_player, CLASSIC_GAME_MODE_ID, 999_999)

    return cache, (player, other_player, third_player)


async def test_the_incremental_state_is_what_we_expect(redis_db, played):
    """Guards the test itself: if the setup is wrong, comparing two wrong states proves nothing."""
    _, players = played
    player, other_player, third_player = players
    seed = daily_seed(datetime.now(UTC))

    state = await snapshot(redis_db, players)

    assert state == {
        "leaderboard:classic": {
            str(player): 3000, str(other_player): 1500, str(third_player): 2500},
        "leaderboard:zen": {str(player): 800, str(other_player): 900},
        f"leaderboard:daily:{seed}": {str(player): 1800, str(other_player): 2400},
    }


async def test_the_rebuild_gives_the_same_rankings_as_the_incremental_state(
    sessions, redis_db, played
):
    cache, players = played
    before = await snapshot(redis_db, players)

    count = await rebuild(sessions, cache)

    assert count >= 3
    assert await snapshot(redis_db, players) == before


async def test_the_rebuild_recreates_the_rankings_after_losing_redis(sessions, redis_db, played):
    cache, players = played
    before = await snapshot(redis_db, players)
    await redis_db.flushdb()

    await rebuild(sessions, cache)

    assert await snapshot(redis_db, players) == before


async def test_the_rebuild_drops_what_the_incremental_state_got_wrong(
    sessions, redis_db, played
):
    cache, players = played
    before = await snapshot(redis_db, players)
    await redis_db.zadd("leaderboard:classic", {"ghost-player": 99999})
    await redis_db.zadd("leaderboard:classic", {str(players[1]): 9999})  # a score that never was

    await rebuild(sessions, cache)

    assert await redis_db.zscore("leaderboard:classic", "ghost-player") is None
    assert await snapshot(redis_db, players) == before


async def test_a_rebuild_is_refused_while_another_holds_the_lock(sessions, redis_db, played):
    cache, players = played
    before = await snapshot(redis_db, players)
    await cache.acquire_rebuild_lock("another-process", 60)

    with pytest.raises(RebuildAlreadyRunning):
        await rebuild(sessions, cache)

    # The refused run touched nothing, and it did not release the lock of the other one
    assert await snapshot(redis_db, players) == before
    assert await redis_db.get("lock:leaderboard-rebuild") == "another-process"


async def test_the_lock_is_released_after_a_rebuild(sessions, redis_db, played):
    cache, _ = played

    await rebuild(sessions, cache)

    assert await redis_db.exists("lock:leaderboard-rebuild") == 0


async def test_the_cli_refuses_to_run_while_another_rebuild_holds_the_lock(
    monkeypatch, redis_db, played
):
    """The CLI builds its own connections: point it at the test Redis, not at the app's."""
    from argparse import Namespace

    from coika_game_service.api.core import cli

    cache, players = played
    before = await snapshot(redis_db, players)
    await cache.acquire_rebuild_lock("another-process", 60)
    real_from_url = redis.asyncio.from_url
    monkeypatch.setattr(
        redis.asyncio, "from_url",
        lambda url, **kwargs: real_from_url(
            redis_url_for_db(url, TEST_DB), decode_responses=True))

    with pytest.raises(RebuildAlreadyRunning):
        await cli.rebuild_leaderboards_async(Namespace(game_mode=None))

    assert await snapshot(redis_db, players) == before
    assert await redis_db.get("lock:leaderboard-rebuild") == "another-process"


@pytest.fixture
def app_on_test_redis(monkeypatch):
    """
    The real app (its real lifespan, with the real PostgreSQL), but with its Redis pointed at
    the test DB: the lifespan builds its own connection from the settings.
    """
    from coika_game_service import main

    real_from_url = redis.asyncio.from_url
    monkeypatch.setattr(
        redis.asyncio, "from_url",
        lambda url, **kwargs: real_from_url(
            redis_url_for_db(url, TEST_DB), decode_responses=True))
    return main


async def test_starting_with_an_empty_redis_rebuilds_the_rankings(
    monkeypatch, redis_db, played, app_on_test_redis
):
    _, players = played
    expected = await snapshot(redis_db, players)
    await redis_db.flushdb()  # Redis lost its data
    monkeypatch.setattr(app_on_test_redis.settings, "REBUILD_ON_EMPTY_REDIS", True)

    app = app_on_test_redis.create_app()
    async with app.router.lifespan_context(app):
        assert await snapshot(redis_db, players) == expected

    assert await redis_db.exists("lock:leaderboard-rebuild") == 0


async def test_starting_with_data_in_redis_does_not_rebuild(
    monkeypatch, redis_db, played, app_on_test_redis
):
    _, players = played
    await redis_db.flushdb()
    await redis_db.set("some:other:key", "x")  # Redis is not empty, but has no rankings
    monkeypatch.setattr(app_on_test_redis.settings, "REBUILD_ON_EMPTY_REDIS", True)

    app = app_on_test_redis.create_app()
    async with app.router.lifespan_context(app):
        assert await snapshot(redis_db, players) == {}


async def test_starting_with_the_option_off_does_not_rebuild(
    monkeypatch, redis_db, played, app_on_test_redis
):
    _, players = played
    await redis_db.flushdb()
    monkeypatch.setattr(app_on_test_redis.settings, "REBUILD_ON_EMPTY_REDIS", False)

    app = app_on_test_redis.create_app()
    async with app.router.lifespan_context(app):
        assert await snapshot(redis_db, players) == {}
