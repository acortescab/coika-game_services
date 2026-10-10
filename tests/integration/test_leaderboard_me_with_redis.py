"""HU-09 against a real Redis: the Lua script, the service and the route together."""
import random
import uuid

import httpx
import pytest
import redis.asyncio

from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.dependencies import bearer_token, current_player
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.player_name_service import fallback_name
from coika_game_service.main import create_app

pytestmark = pytest.mark.integration

TOKEN = "caller-access-token"
PLAYERS = [uuid.uuid4() for _ in range(10)]  # PLAYERS[0] has the best score, [9] the worst


class FakeNames:
    def __init__(self):
        self.calls = []

    async def get_names(self, player_ids, token):
        self.calls.append(list(player_ids))
        return {pid: fallback_name(pid) for pid in player_ids}


@pytest.fixture
async def ranking():
    """
    A daily ranking of 10 players on a made-up date, so no real ranking is touched. Yields
    (date, redis client, names) and removes the key afterwards.
    """
    client = redis.asyncio.from_url(Settings().REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis no disponible: {exc!r}")

    date = f"{random.randint(1900, 1969)}{random.randint(1, 12):02d}{random.randint(1, 28):02d}"
    key = f"leaderboard:daily:{date}"
    await client.delete(key)
    await client.zadd(key, {str(p): 1000 - i * 100 for i, p in enumerate(PLAYERS)})
    try:
        yield date, client, FakeNames()
    finally:
        await client.delete(key)
        await client.aclose()


def app_for(client, names, player):
    app = create_app()
    service = LeaderboardService(RedisRepository(client), names)
    app.dependency_overrides[get_leaderboard_service] = lambda: service
    app.dependency_overrides[bearer_token] = lambda: TOKEN
    app.dependency_overrides[current_player] = lambda: player
    return app


async def get(app, **params):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.get("/game-modes/daily/leaderboard/me", params=params)


async def test_returns_my_position_my_score_and_n_neighbours_above_and_below(ranking):
    date, client, names = ranking

    response = await get(app_for(client, names, PLAYERS[5]), around=2, date=date)

    assert response.status_code == 200
    body = response.json()
    assert [(r["position"], r["player_id"], r["score"]) for r in body] == [
        (4, str(PLAYERS[3]), 700),
        (5, str(PLAYERS[4]), 600),
        (6, str(PLAYERS[5]), 500),  # me
        (7, str(PLAYERS[6]), 400),
        (8, str(PLAYERS[7]), 300),
    ]
    assert all(r["name"] for r in body)


async def test_at_the_top_there_is_nobody_above(ranking):
    date, client, names = ranking

    body = (await get(app_for(client, names, PLAYERS[0]), around=2, date=date)).json()

    assert [r["position"] for r in body] == [1, 2, 3]
    assert body[0]["player_id"] == str(PLAYERS[0])


async def test_at_the_bottom_there_is_nobody_below(ranking):
    date, client, names = ranking

    body = (await get(app_for(client, names, PLAYERS[9]), around=2, date=date)).json()

    assert [r["position"] for r in body] == [8, 9, 10]
    assert body[-1]["player_id"] == str(PLAYERS[9])


async def test_around_larger_than_the_ranking_returns_all_of_it(ranking):
    date, client, names = ranking

    body = (await get(app_for(client, names, PLAYERS[4]), around=50, date=date)).json()

    assert [r["position"] for r in body] == list(range(1, 11))


async def test_everybody_is_named_in_one_single_lookup(ranking):
    date, client, names = ranking

    await get(app_for(client, names, PLAYERS[5]), around=2, date=date)

    assert len(names.calls) == 1
    assert len(names.calls[0]) == 5


async def test_a_player_without_score_that_day_is_404(ranking):
    date, client, names = ranking

    response = await get(app_for(client, names, uuid.uuid4()), around=2, date=date)

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found in the leaderboard"}
    assert names.calls == []


async def test_a_day_whose_ranking_expired_is_404_not_an_empty_list(ranking):
    _, client, names = ranking

    response = await get(app_for(client, names, PLAYERS[0]), around=2, date="19000101")

    assert response.status_code == 404


async def test_a_date_on_a_non_daily_mode_is_422(ranking):
    _, client, names = ranking
    transport = httpx.ASGITransport(app=app_for(client, names, PLAYERS[0]))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get(
            "/game-modes/classic/leaderboard/me", params={"date": "20261010"})

    assert response.status_code == 422


async def test_a_second_call_gets_a_consistent_answer(ranking):
    """Same script, registered once and reused with EVALSHA: same result both times."""
    date, client, names = ranking
    app = app_for(client, names, PLAYERS[5])

    first = (await get(app, around=1, date=date)).json()
    second = (await get(app, around=1, date=date)).json()

    assert first == second
