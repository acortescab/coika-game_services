import uuid
from types import SimpleNamespace

import pytest
from redis.exceptions import RedisError

from coika_game_service.api.core.exceptions import MatchNotFound, MatchNotOpen, ScoreAlreadyExists
from coika_game_service.api.db.models import GameModeName, MatchStatus
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services.score_service import ScoreService

PLAYER = uuid.uuid4()
MATCH_ID = uuid.uuid4()
MODE_ID = uuid.uuid4()
PAYLOAD = CreateScoreRequest(score=1500, pieces_dropped=120, highest_tier=7)


class Journal:
    """One shared list, so a test can check in which order things happened."""

    def __init__(self):
        self.events = []

    def add(self, *event):
        self.events.append(event)

    def names(self):
        return [event[0] for event in self.events]


class FakeMatchRepo:
    def __init__(self, match):
        self.match = match

    async def get_for_update(self, match_id):
        return self.match

    async def finish_match(self, match_id):
        self.match.status = MatchStatus.FINISHED


class FakeScoreRepo:
    def __init__(self, journal, best=None, stored=None):
        self.journal = journal
        self.best = best
        self.stored = stored

    async def get_best_score(self, game_mode_id, player_id):
        return self.best

    async def create_score(self, match_id, payload):
        self.journal.add("create_score")
        return SimpleNamespace(
            match_id=match_id,
            score=payload.score,
            pieces_dropped=payload.pieces_dropped,
            highest_tier=payload.highest_tier,
        )

    async def get_by_match_id(self, match_id, use_writer=False):
        return self.stored


class FakeGameModeRepo:
    def __init__(self, name):
        self.name = name

    async def get_game_mode(self, game_mode_id):
        return SimpleNamespace(id=game_mode_id, game_mode=self.name)


class FakeCache:
    def __init__(self, journal):
        self.journal = journal

    async def update_max_score(self, player_id, game_mode, score):
        self.journal.add("redis_classic", player_id, game_mode, score)

    async def update_max_score_daily(self, player_id, game_mode, score, seed):
        self.journal.add("redis_daily", player_id, game_mode, score, seed)


class FakeSession:
    def __init__(self, journal, fail=False):
        self.journal = journal
        self.fail = fail

    async def commit(self):
        if self.fail:
            raise RuntimeError("commit failed")
        self.journal.add("commit")


def match(status=MatchStatus.IN_PROGRESS, seed=None, player=PLAYER):
    return SimpleNamespace(
        id=MATCH_ID, player_id=player, game_mode_id=MODE_ID, status=status, seed=seed
    )


def build(
    mode=GameModeName.CLASSIC, best=None, status=MatchStatus.IN_PROGRESS, seed=None,
    stored=None, owner=PLAYER, cache=None, fail_commit=False,
):
    journal = Journal()
    service = ScoreService(
        FakeScoreRepo(journal, best=best, stored=stored),
        FakeMatchRepo(match(status, seed, owner)),
        cache if cache is not None else FakeCache(journal),
        FakeGameModeRepo(mode),
        FakeSession(journal, fail=fail_commit),
    )
    return service, journal


async def test_the_first_score_goes_to_the_ranking_of_its_mode_by_name():
    service, journal = build(mode=GameModeName.CLASSIC, best=None)

    await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert ("redis_classic", PLAYER, GameModeName.CLASSIC, 1500) in journal.events


async def test_every_non_daily_mode_writes_the_all_time_ranking():
    for mode in (GameModeName.CLASSIC, GameModeName.ZEN):
        service, journal = build(mode=mode)

        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

        assert ("redis_classic", PLAYER, mode, 1500) in journal.events


async def test_a_daily_score_goes_to_the_ranking_of_the_seed_date_of_the_match():
    """The seed is the UTC date the match started, not the date of the submission."""
    service, journal = build(mode=GameModeName.DAILY, seed=20261009)

    await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert ("redis_daily", PLAYER, GameModeName.DAILY, 1500, 20261009) in journal.events
    assert "redis_classic" not in journal.names()


async def test_a_better_score_updates_the_ranking():
    service, journal = build(best=1000)

    await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert "redis_classic" in journal.names()


@pytest.mark.parametrize("best", [1500, 9999], ids=["equal", "worse"])
async def test_a_score_that_does_not_improve_the_best_does_not_touch_redis(best):
    service, journal = build(best=best)

    await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert journal.names() == ["create_score", "commit"]


async def test_redis_is_written_after_the_commit_never_before():
    service, journal = build()

    await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert journal.names() == ["create_score", "commit", "redis_classic"]


async def test_if_the_commit_fails_redis_is_not_written():
    service, journal = build(fail_commit=True)

    with pytest.raises(RuntimeError):
        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert "redis_classic" not in journal.names()


async def test_a_retry_returns_the_stored_score_and_writes_nothing():
    stored = SimpleNamespace(score=1500, pieces_dropped=120, highest_tier=7)
    service, journal = build(status=MatchStatus.FINISHED, stored=stored)

    result = await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert result is stored
    assert journal.events == []


async def test_a_retry_with_other_figures_is_rejected_and_writes_nothing():
    stored = SimpleNamespace(score=1, pieces_dropped=1, highest_tier=1)
    service, journal = build(status=MatchStatus.FINISHED, stored=stored)

    with pytest.raises(ScoreAlreadyExists):
        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert journal.events == []


@pytest.mark.parametrize("status", [MatchStatus.ABANDONED, MatchStatus.REJECTED])
async def test_a_match_that_is_not_open_is_rejected(status):
    service, journal = build(status=status)

    with pytest.raises(MatchNotOpen):
        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert journal.events == []


async def test_a_match_of_another_player_is_not_found():
    service, journal = build(owner=uuid.uuid4())

    with pytest.raises(MatchNotFound):
        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert journal.events == []


class DownRedis:
    """A Redis client where every command fails."""

    async def zadd(self, *args, **kwargs):
        raise RedisError("redis down")

    def pipeline(self, transaction=True):
        raise RedisError("redis down")


async def test_redis_down_does_not_lose_the_score_nor_fail_the_submission():
    """With the real repository over a dead Redis: the score is committed and returned."""
    for mode, seed in ((GameModeName.CLASSIC, None), (GameModeName.DAILY, 20261010)):
        journal = Journal()
        service = ScoreService(
            FakeScoreRepo(journal),
            FakeMatchRepo(match(seed=seed)),
            RedisRepository(DownRedis()),
            FakeGameModeRepo(mode),
            FakeSession(journal),
        )

        score = await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

        assert score.score == 1500
        assert journal.names() == ["create_score", "commit"]
