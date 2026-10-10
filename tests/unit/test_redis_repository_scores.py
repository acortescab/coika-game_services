import uuid

import pytest
from redis.exceptions import RedisError

from coika_game_service.api.core.config import settings
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.repositories.redis_repository import RedisRepository

GAME_MODE = GameModeName.CLASSIC
PLAYER = uuid.uuid4()


class FakeRedis:
    """
    Records the commands. zadd and zrevrange copy the redis-py signature, so an option redis does
    not have (like `ex` on zadd) fails here as it would against a real server.
    """

    def __init__(self, fail=False, ranking=None):
        self.fail = fail
        self.ranking = ranking or []
        self.commands = []

    def _check(self):
        if self.fail:
            raise RedisError("redis down")

    async def zadd(self, name, mapping, nx=False, xx=False, ch=False, incr=False, gt=False, lt=False):
        self._check()
        self.commands.append(("zadd", name, mapping, {"gt": gt}))

    async def zrevrange(self, name, start, end, withscores=False):
        self._check()
        self.commands.append(("zrevrange", name, start, end, withscores))
        return self.ranking

    def pipeline(self, transaction=True):
        self._check()
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.queued = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def zadd(self, name, mapping, nx=False, xx=False, ch=False, incr=False, gt=False, lt=False):
        self.queued.append(("zadd", name, mapping, {"gt": gt}))

    def expire(self, name, time):
        self.queued.append(("expire", name, time))

    async def execute(self):
        self.redis._check()
        self.redis.commands.extend(self.queued)


async def test_the_ranking_keys_use_the_name_of_the_mode_not_an_id():
    redis = FakeRedis()
    repo = RedisRepository(redis)

    await repo.update_max_score(PLAYER, GameModeName.ZEN, 10)
    await repo.update_max_score_daily(PLAYER, GameModeName.DAILY, 10, 20261010)

    keys = [command[1] for command in redis.commands if command[0] == "zadd"]
    assert keys == ["leaderboard:zen", "leaderboard:daily:20261010"]


async def test_the_all_time_score_keeps_the_best_and_never_expires():
    redis = FakeRedis()

    await RedisRepository(redis).update_max_score(PLAYER, GAME_MODE, 900)

    assert redis.commands == [("zadd", f"leaderboard:{GAME_MODE}", {str(PLAYER): 900}, {"gt": True})]


async def test_the_daily_score_keeps_the_best_and_sets_the_expiry():
    redis = FakeRedis()

    await RedisRepository(redis).update_max_score_daily(PLAYER, GAME_MODE, 900, 20261010)

    key = f"leaderboard:{GAME_MODE}:20261010"
    assert redis.commands == [
        ("zadd", key, {str(PLAYER): 900}, {"gt": True}),
        ("expire", key, settings.SCORES_TTL_SECONDS),
    ]


async def test_writing_scores_survives_redis_being_down():
    repo = RedisRepository(FakeRedis(fail=True))

    await repo.update_max_score(PLAYER, GAME_MODE, 900)
    await repo.update_max_score_daily(PLAYER, GAME_MODE, 900, 20261010)


async def test_the_ranking_is_read_from_best_to_worst_with_scores_and_the_limit():
    redis = FakeRedis(ranking=[(b"a", 20.0), (b"b", 10.0)])

    ranking = await RedisRepository(redis).get_leaderboard(GAME_MODE, limit=2)

    assert ranking == [(b"a", 20.0), (b"b", 10.0)]
    assert redis.commands == [("zrevrange", f"leaderboard:{GAME_MODE}", 0, 1, True)]


async def test_the_daily_ranking_reads_the_key_of_that_date():
    redis = FakeRedis()

    await RedisRepository(redis).get_daily_leaderboard(GAME_MODE, limit=5, seed="20261009")

    assert redis.commands == [("zrevrange", f"leaderboard:{GAME_MODE}:20261009", 0, 4, True)]


@pytest.mark.parametrize("method", ["get_leaderboard", "get_daily_leaderboard"])
async def test_a_missing_or_unreachable_ranking_is_an_empty_list(method):
    """No key (expired day) and Redis down both read as an empty ranking, never an error."""
    repo = RedisRepository(FakeRedis(fail=True))

    assert await getattr(repo, method)(GAME_MODE) == []
    assert await RedisRepository(FakeRedis()).get_leaderboard(GAME_MODE) == []
