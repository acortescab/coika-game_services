import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from redis.exceptions import RedisError

from coika_game_service.api.core.dependencies import bearer_token, current_player
from coika_game_service.api.core.exceptions import (
    CacheDown,
    InvalidLeaderboardRank,
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.core.game_modes import daily_seed
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.player_name_service import fallback_name
from coika_game_service.main import create_app

TOKEN = "caller-access-token"
PLAYER = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
OTHER = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")


# --- Repository ------------------------------------------------------------------------------


class FakeRedis:
    """register_script gives a callable that returns the canned Lua answer and records the call."""

    def __init__(self, answer=None, fail=False):
        self.answer = answer
        self.fail = fail
        self.calls = []
        self.registered = 0

    def register_script(self, source):
        self.registered += 1

        async def script(keys, args):
            if self.fail:
                raise RedisError("redis down")
            self.calls.append((keys, args))
            return self.answer

        return script


async def test_the_window_comes_as_pairs_with_float_scores_and_the_first_position():
    # Lua: index 3 (0-based) is the first entry -> position 4
    redis = FakeRedis(answer=[3, ["ana", "900", "bob", "750.5"]])

    first, entries = await RedisRepository(redis).get_leaderboard_me(PLAYER, "classic", 2)

    assert first == 4
    assert entries == [("ana", 900.0), ("bob", 750.5)]


async def test_the_all_time_window_reads_the_all_time_key_with_the_player_and_the_limit():
    redis = FakeRedis(answer=[0, ["ana", "1"]])

    await RedisRepository(redis).get_leaderboard_me(PLAYER, "classic", 7)

    assert redis.calls == [(["leaderboard:classic"], [str(PLAYER), 7])]


async def test_the_daily_window_reads_the_key_of_that_date():
    redis = FakeRedis(answer=[0, ["ana", "1"]])

    await RedisRepository(redis).get_daily_leaderboard_me(PLAYER, "daily", 7, "20261010")

    assert redis.calls == [(["leaderboard:daily:20261010"], [str(PLAYER), 7])]


async def test_the_daily_window_defaults_to_today():
    redis = FakeRedis(answer=[0, ["ana", "1"]])

    await RedisRepository(redis).get_daily_leaderboard_me(PLAYER, "daily")

    # Either side of a possible midnight during the test
    now = datetime.now(UTC)
    today = {daily_seed(now - timedelta(seconds=5)), daily_seed(now + timedelta(seconds=5))}
    assert redis.calls[0][0][0] in {f"leaderboard:daily:{seed}" for seed in today}


async def test_a_player_without_score_is_invalid_leaderboard_rank():
    repo = RedisRepository(FakeRedis(answer=None))

    with pytest.raises(InvalidLeaderboardRank):
        await repo.get_leaderboard_me(PLAYER, "classic")
    with pytest.raises(InvalidLeaderboardRank):
        await repo.get_daily_leaderboard_me(PLAYER, "daily", seed="20261010")


async def test_redis_down_is_cache_down():
    repo = RedisRepository(FakeRedis(fail=True))

    with pytest.raises(CacheDown):
        await repo.get_leaderboard_me(PLAYER, "classic")
    with pytest.raises(CacheDown):
        await repo.get_daily_leaderboard_me(PLAYER, "daily", seed="20261010")


async def test_rank_and_window_are_one_single_round_trip_to_redis():
    """HU-09: consistent operations in one round to Redis (here one atomic Lua call)."""
    redis = FakeRedis(answer=[0, ["ana", "1"]])

    await RedisRepository(redis).get_leaderboard_me(PLAYER, "classic", 5)

    assert len(redis.calls) == 1


async def test_the_script_is_registered_once_not_on_every_request():
    redis = FakeRedis(answer=[0, ["ana", "1"]])
    repo = RedisRepository(redis)

    for _ in range(3):
        await repo.get_leaderboard_me(PLAYER, "classic")

    assert redis.registered == 1


# --- Service ---------------------------------------------------------------------------------


class FakeCache:
    def __init__(self, first=1, entries=None, error=None):
        self.first = first
        self.entries = entries or []
        self.error = error
        self.calls = []

    async def get_leaderboard_me(self, player_id, game_mode, limit=25):
        self.calls.append(("classic", player_id, game_mode, limit))
        if self.error:
            raise self.error
        return self.first, self.entries

    async def get_daily_leaderboard_me(self, player_id, game_mode, limit=25, seed=None):
        self.calls.append(("daily", player_id, game_mode, limit, seed))
        if self.error:
            raise self.error
        return self.first, self.entries


class FakeNames:
    """Resolves from a dict (fallback name otherwise) and records every lookup."""

    def __init__(self, names=None):
        self.names = names or {}
        self.calls = []

    async def get_names(self, player_ids, token):
        self.calls.append((list(player_ids), token))
        return {pid: self.names.get(pid, fallback_name(pid)) for pid in player_ids}


def service_with(cache):
    return LeaderboardService(cache, FakeNames())


async def test_positions_start_at_the_real_rank_of_the_first_entry_not_at_1():
    cache = FakeCache(first=41, entries=[(str(OTHER), 900.0), (str(PLAYER), 800.0)])

    ranking = await service_with(cache).get_leaderboard_me(
        TOKEN, PLAYER, GameModeName.CLASSIC, 5)

    assert [(r.position, r.player_id) for r in ranking] == [
        (41, str(OTHER)), (42, str(PLAYER)),
    ]


async def test_everyone_in_the_window_is_named_in_one_single_lookup():
    """HU-09: me and my neighbours are resolved with PlayerNameService in one query."""
    above, below = uuid.uuid4(), uuid.uuid4()
    cache = FakeCache(
        first=10, entries=[(str(above), 900.0), (str(PLAYER), 800.0), (str(below), 700.0)])
    names = FakeNames({above: "Above", PLAYER: "Me", below: "Below"})

    ranking = await LeaderboardService(cache, names).get_leaderboard_me(
        TOKEN, PLAYER, GameModeName.CLASSIC, 1)

    assert names.calls == [([above, PLAYER, below], TOKEN)]
    assert [(r.position, r.name, r.score) for r in ranking] == [
        (10, "Above", 900), (11, "Me", 800), (12, "Below", 700),
    ]


async def test_a_neighbour_whose_name_cannot_be_resolved_gets_the_fallback_name():
    other = uuid.uuid4()
    cache = FakeCache(entries=[(str(other), 900.0), (str(PLAYER), 800.0)])

    ranking = await service_with(cache).get_leaderboard_me(
        TOKEN, PLAYER, GameModeName.CLASSIC, 1)

    assert ranking[0].name == fallback_name(other)


async def test_my_own_entry_is_in_the_window_with_my_score():
    cache = FakeCache(first=5, entries=[(str(OTHER), 900.0), (str(PLAYER), 800.0)])

    ranking = await service_with(cache).get_leaderboard_me(
        TOKEN, PLAYER, GameModeName.CLASSIC, 1)

    mine = [r for r in ranking if r.player_id == str(PLAYER)]
    assert [(r.position, r.score) for r in mine] == [(6, 800)]


async def test_the_daily_window_defaults_to_today_in_utc():
    cache = FakeCache(entries=[(str(PLAYER), 800.0)])
    before = daily_seed(datetime.now(UTC))

    await service_with(cache).get_leaderboard_me(TOKEN, PLAYER, GameModeName.DAILY, 5)

    after = daily_seed(datetime.now(UTC))
    assert str(cache.calls[0][4]) in {str(before), str(after)}


@pytest.mark.parametrize("date", ["20261340", "20260230", "2026101", "abcdefgh", "202610101"])
async def test_a_daily_date_that_is_not_a_real_date_is_rejected(date):
    cache = FakeCache()

    with pytest.raises(LeaderboardInvalidDate):
        await service_with(cache).get_leaderboard_me(
            TOKEN, PLAYER, GameModeName.DAILY, 5, date)

    assert cache.calls == []


async def test_the_daily_window_uses_the_given_date():
    cache = FakeCache(entries=[(str(PLAYER), 800.0)])

    await service_with(cache).get_leaderboard_me(
        TOKEN, PLAYER, GameModeName.DAILY, 5, "20261010")

    assert cache.calls == [("daily", PLAYER, GameModeName.DAILY, 5, "20261010")]


async def test_a_date_on_a_non_daily_mode_is_refused():
    cache = FakeCache()

    with pytest.raises(NonDailyLeaderboardWithDate):
        await service_with(cache).get_leaderboard_me(
            TOKEN, PLAYER, GameModeName.CLASSIC, 5, "20261010")

    assert cache.calls == []


@pytest.mark.parametrize("mode", [GameModeName.CLASSIC, GameModeName.DAILY])
async def test_a_player_without_score_reaches_the_caller_as_invalid_leaderboard_rank(mode):
    cache = FakeCache(error=InvalidLeaderboardRank("nobody"))

    with pytest.raises(InvalidLeaderboardRank):
        await service_with(cache).get_leaderboard_me(TOKEN, PLAYER, mode, 5)


# --- HTTP ------------------------------------------------------------------------------------


class ErroringService:
    def __init__(self, error):
        self.error = error

    async def get_leaderboard_me(self, *args):
        raise self.error

    async def get_leaderboard(self, *args):
        raise self.error


def build_app(service):
    app = create_app()
    app.dependency_overrides[get_leaderboard_service] = lambda: service
    app.dependency_overrides[bearer_token] = lambda: TOKEN
    app.dependency_overrides[current_player] = lambda: PLAYER
    return app


async def get(app, path):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_a_player_without_score_is_a_404_not_a_500():
    app = build_app(ErroringService(InvalidLeaderboardRank("x")))

    response = await get(app, "/game-modes/classic/leaderboard/me")

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found in the leaderboard"}


@pytest.mark.parametrize("path", ["/game-modes/classic/leaderboard/me",
                                  "/game-modes/classic/leaderboard"])
async def test_redis_down_is_a_503_with_retry_after(path):
    app = build_app(ErroringService(CacheDown()))

    response = await get(app, path)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


async def test_around_is_between_1_and_50():
    app = build_app(ErroringService(InvalidLeaderboardRank("x")))

    assert (await get(app, "/game-modes/classic/leaderboard/me?around=0")).status_code == 422
    assert (await get(app, "/game-modes/classic/leaderboard/me?around=51")).status_code == 422

