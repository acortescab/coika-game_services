import uuid
from types import SimpleNamespace

import pytest
import redis.asyncio

from coika_game_service.api.core.config import Settings
from coika_game_service.api.repositories.redis_repository import RedisRepository
from tests.integration.conftest import redis_url_for_db

pytestmark = pytest.mark.integration

TEST_DB = 15


@pytest.fixture
async def client():
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


def row(game_mode, score, seed=None, player=None):
    """One SQL result row: what best_scores() would yield."""
    return SimpleNamespace(
        player_id=player or uuid.uuid4(), game_mode=game_mode, seed=seed, best_score=score)


def as_batches(rows, size):
    """An async generator of lists of `size` rows, like best_scores(batch_size=size)."""

    async def generator():
        for start in range(0, len(rows), size):
            yield rows[start:start + size]

    return generator()


async def test_a_ranking_split_across_batches_ends_up_complete(client):
    rows = [row("classic", 100 + i) for i in range(7)]

    repo = RedisRepository(client)
    batches = as_batches(rows, 3)

    count = await repo.rebuild_rankings(batches)
    key = repo.ranking_key(rows[0].game_mode, rows[0].seed)
    
    assert count == 1
    assert await client.zcard("leaderboard:classic") == 7
    assert await client.zrevrange(key, 0, 0, withscores=True)


async def test_several_rankings_in_one_run_do_not_mix(client):
    rows = [
        row("classic", 500),
        row("classic", 800),
        row("daily", 1200, seed=20261010),
        row("daily", 900, seed=20261010),
        row("daily", 700, seed=20261009),
        row("zen", 400),
    ]

    repo = RedisRepository(client)
    batches = as_batches(rows, 4)

    count = await repo.rebuild_rankings(batches)

    assert count == 4

    assert await client.zcard("leaderboard:classic") == 2
    assert await client.zcard("leaderboard:daily:20261010") == 2
    assert await client.zcard("leaderboard:daily:20261009") == 1
    assert await client.zcard("leaderboard:zen") == 1

    assert await client.exists("*:tmp") == 0


async def test_a_dirty_tmp_key_from_a_dead_run_does_not_contaminate(client):
    await client.zadd("leaderboard:classic:tmp", {"ghost-player": 99999})
    rows = [row("classic", 100), row("classic", 200)]

    repo = RedisRepository(client)
    batches = as_batches(rows, 4)

    await repo.rebuild_rankings(batches)

    score = await client.zscore("leaderboard:classic", "ghost-player")
    assert score is None
    assert await client.zcard("leaderboard:classic") == 2


async def test_the_published_ranking_replaces_the_old_one_atomically(client):
    await client.zadd("leaderboard:classic", {"old-player": 1})
    rows = [row("classic", 100)]

    repo = RedisRepository(client)
    batches = as_batches(rows, 4)
    
    count = await repo.rebuild_rankings(batches)
    
    score = await client.zscore("leaderboard:classic", "old-player")
    assert score is None

    score = await client.zscore("leaderboard:classic", "old-player")
    assert count == 1


async def test_no_rows_publishes_nothing_and_touches_nothing(client):
    await client.zadd("leaderboard:classic", {"old-player": 1})

    repo = RedisRepository(client)
    batches = as_batches([], 3)

    count = await repo.rebuild_rankings(batches)

    assert count == 0
    assert await client.exists("leaderboard:classic") == 1


async def test_a_daily_row_without_seed_is_a_value_error(client):
    rows = [row("daily", 100, seed=None)]

    repo = RedisRepository(client)
    batches = as_batches(rows, 3)

    with pytest.raises(ValueError): 
        await repo.rebuild_rankings(batches)
        assert await client.exists("leaderboard:*") == 0

async def test_rebuild_ttls(client):
    rows = [
        row("classic", 500),
        row("daily", 1200, seed=20261010),
        row("zen", 400),
    ]

    repo = RedisRepository(client)
    batches = as_batches(rows, 3)

    await repo.rebuild_rankings(batches)

    assert await client.ttl("leaderboard:daily:20261010") > 0
    assert await client.ttl("leaderboard:classic") == -1
    assert await client.ttl("leaderboard:zen") == -1

async def test_rebuild_ttl_daily(client):
    rows = [row("daily", 1200, seed=20261010)]

    repo = RedisRepository(client)
    batches = as_batches(rows, 3)

    await repo.rebuild_rankings(batches)

    assert await client.ttl("leaderboard:daily:20261010") > 0

async def test_the_rebuild_lock_is_taken_by_one_holder_at_a_time(client):
    repo = RedisRepository(client)

    assert await repo.acquire_rebuild_lock("first", 60) is True
    assert await repo.acquire_rebuild_lock("second", 60) is False
    assert 0 < await client.ttl("lock:leaderboard-rebuild") <= 60


async def test_only_the_holder_can_release_the_rebuild_lock(client):
    repo = RedisRepository(client)
    await repo.acquire_rebuild_lock("first", 60)

    await repo.release_rebuild_lock("somebody-else")
    assert await repo.acquire_rebuild_lock("second", 60) is False

    await repo.release_rebuild_lock("first")
    assert await repo.acquire_rebuild_lock("second", 60) is True
