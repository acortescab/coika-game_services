import uuid
from datetime import UTC, datetime

import pytest

from coika_game_service.api.core.exceptions import (
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.game_modes import daily_seed
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.player_name_service import fallback_name

TOKEN = "caller-access-token"


class FakeCache:
    """Returns canned rankings and records how it was asked."""

    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    async def get_leaderboard(self, game_mode, limit=50):
        self.calls.append(("classic", game_mode, limit))
        return self.results

    async def get_daily_leaderboard(self, game_mode, limit=50, seed=None):
        self.calls.append(("daily", game_mode, limit, seed))
        return self.results


class FakeNames:
    """Stands in for PlayerNameService: resolves from a dict, fallback name otherwise."""

    def __init__(self, names=None):
        self.names = names or {}
        self.calls = []

    async def get_names(self, player_ids, token):
        self.calls.append((list(player_ids), token))
        return {pid: self.names.get(pid, fallback_name(pid)) for pid in player_ids}


def build(results=None, names=None):
    cache = FakeCache(results)
    name_service = FakeNames(names)
    service = LeaderboardService(cache, name_service)
    return service, cache, name_service


async def test_classic_ranking_keeps_the_order_and_adds_the_names():
    ana, luis = uuid.uuid4(), uuid.uuid4()
    service, _, _ = build(
        results=[(str(luis), 1500.0), (str(ana), 1200.0)],
        names={ana: "Ana", luis: "Luis"},
    )

    ranking = await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert ranking == [
        LeaderboardResponse(position=1, player_id=str(luis), name="Luis", score=1500),
        LeaderboardResponse(position=2, player_id=str(ana), name="Ana", score=1200),
    ]


async def test_positions_follow_the_order_of_the_ranking_starting_at_1_even_with_ties():
    """Tied scores still get different positions: the order Redis gives is the tie-break."""
    first, second, third, fourth = (uuid.uuid4() for _ in range(4))
    service, _, _ = build(
        results=[(str(first), 900.0), (str(second), 700.0), (str(third), 700.0), (str(fourth), 5.0)]
    )

    ranking = await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert [(r.position, r.player_id) for r in ranking] == [
        (1, str(first)),
        (2, str(second)),
        (3, str(third)),
        (4, str(fourth)),
    ]


async def test_scores_come_back_as_int_even_though_redis_stores_floats():
    ana = uuid.uuid4()
    service, _, _ = build(results=[(str(ana), 1500.0)], names={ana: "Ana"})

    ranking = await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert ranking[0].score == 1500
    assert isinstance(ranking[0].score, int)


async def test_names_are_asked_with_uuids_and_the_callers_token():
    ana, luis = uuid.uuid4(), uuid.uuid4()
    service, _, names = build(results=[(str(ana), 10.0), (str(luis), 5.0)])

    await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert names.calls == [([ana, luis], TOKEN)]


async def test_ids_that_redis_returns_as_bytes_are_accepted():
    """Without decode_responses the sorted-set members arrive as bytes."""
    ana = uuid.uuid4()
    service, _, names = build(results=[(str(ana).encode(), 1500.0)], names={ana: "Ana"})

    ranking = await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert ranking == [
        LeaderboardResponse(position=1, player_id=str(ana), name="Ana", score=1500)
    ]
    assert names.calls == [([ana], TOKEN)]


async def test_a_player_without_a_resolved_name_gets_the_fallback_name():
    ghost = uuid.uuid4()
    service, _, _ = build(results=[(str(ghost), 10.0)])

    ranking = await service.get_leaderboard(TOKEN, GameModeName.CLASSIC)

    assert ranking[0].name == fallback_name(ghost)


async def test_an_empty_ranking_is_an_empty_list_not_an_error():
    """An expired or never-played ranking has no key in Redis: the answer is [], not a 404."""
    service, _, _ = build(results=[])

    assert await service.get_leaderboard(TOKEN, GameModeName.CLASSIC) == []
    assert await service.get_leaderboard(TOKEN, GameModeName.DAILY, date="20200101") == []


async def test_the_limit_is_passed_to_the_cache():
    service, cache, _ = build()

    await service.get_leaderboard(TOKEN, GameModeName.CLASSIC, limit=10)

    assert cache.calls == [("classic", GameModeName.CLASSIC, 10)]


@pytest.mark.parametrize("mode", [GameModeName.CLASSIC, GameModeName.ZEN])
async def test_every_non_daily_mode_reads_its_all_time_ranking(mode):
    service, cache, _ = build()

    await service.get_leaderboard(TOKEN, mode)

    assert cache.calls == [("classic", mode, 50)]


async def test_a_non_daily_mode_rejects_a_date():
    service, cache, _ = build()

    with pytest.raises(NonDailyLeaderboardWithDate):
        await service.get_leaderboard(TOKEN, GameModeName.CLASSIC, date="20261010")

    assert cache.calls == []


async def test_the_daily_ranking_uses_the_requested_date():
    service, cache, _ = build()

    await service.get_leaderboard(TOKEN, GameModeName.DAILY, limit=20, date="20261009")

    assert cache.calls == [("daily", GameModeName.DAILY, 20, "20261009")]


async def test_the_daily_ranking_defaults_to_today_in_utc():
    service, cache, _ = build()
    before = daily_seed(datetime.now(UTC))

    await service.get_leaderboard(TOKEN, GameModeName.DAILY)

    after = daily_seed(datetime.now(UTC))
    (_, _, _, seed), = cache.calls
    assert before <= int(seed) <= after


@pytest.mark.parametrize("date", ["20261340", "20260230", "abcdefgh", "2026101"])
async def test_a_daily_date_that_is_not_a_real_date_is_rejected(date):
    service, cache, _ = build()

    with pytest.raises(LeaderboardInvalidDate):
        await service.get_leaderboard(TOKEN, GameModeName.DAILY, date=date)

    assert cache.calls == []
