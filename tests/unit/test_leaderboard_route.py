import uuid
from types import SimpleNamespace

import httpx
import pytest

from coika_game_service.api.core.dependencies import bearer_token
from coika_game_service.api.core.exceptions import (
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.main import create_app

TOKEN = "caller-access-token"
GAME_MODE = GameModeName.CLASSIC
ANA = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
LUIS = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")


class FakeLeaderboardService:
    """Stands in for LeaderboardService: records the call and returns or raises what is set."""

    def __init__(self, result=None, error=None):
        self.result = result if result is not None else []
        self.error = error
        self.calls = []

    async def get_leaderboard(self, token, game_mode, limit, date):
        self.calls.append((token, game_mode, limit, date))
        if self.error:
            raise self.error
        return self.result


def build_app(service: FakeLeaderboardService, *, authenticated: bool = True):
    app = create_app()
    app.dependency_overrides[get_leaderboard_service] = lambda: service
    if authenticated:
        app.dependency_overrides[bearer_token] = lambda: TOKEN
    else:
        # No token is sent, so the key set is never consulted; it only has to exist.
        app.state.jwks = SimpleNamespace()
    return app


async def get(app, game_mode=GAME_MODE, **params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(f"/game-modes/{game_mode}/leaderboard", params=params)


async def test_returns_200_with_the_ranking_as_a_list():
    service = FakeLeaderboardService(
        [
            LeaderboardResponse(position=1, player_id=str(LUIS), name="Luis", score=1500),
            LeaderboardResponse(position=2, player_id=str(ANA), name="Ana", score=1200),
        ]
    )

    response = await get(build_app(service))

    assert response.status_code == 200
    assert response.json() == [
        {"position": 1, "player_id": str(LUIS), "name": "Luis", "score": 1500},
        {"position": 2, "player_id": str(ANA), "name": "Ana", "score": 1200},
    ]


async def test_an_empty_ranking_is_200_with_an_empty_list():
    response = await get(build_app(FakeLeaderboardService([])))

    assert response.status_code == 200
    assert response.json() == []


async def test_defaults_are_limit_50_and_no_date():
    service = FakeLeaderboardService()

    await get(build_app(service))

    assert service.calls == [(TOKEN, GAME_MODE, 50, None)]


async def test_limit_and_date_reach_the_service_with_the_callers_token():
    service = FakeLeaderboardService()

    await get(build_app(service), limit=10, date="20261009")

    assert service.calls == [(TOKEN, GAME_MODE, 10, "20261009")]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "many"},
        {"date": "2026-10-09"},
        {"date": "2026100"},
        {"date": "abcdefgh"},
    ],
    ids=[
        "limit-0", 
        "limit-101", 
        "limit-not-a-number", 
        "date-dashes", 
        "date-7-digits", 
        "date-letters"
    ],
)
async def test_invalid_query_params_are_422_and_the_service_is_not_called(params):
    service = FakeLeaderboardService()

    response = await get(build_app(service), **params)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("mode", list(GameModeName))
async def test_every_game_mode_name_is_accepted(mode):
    service = FakeLeaderboardService()

    response = await get(build_app(service), game_mode=mode.value)

    assert response.status_code == 200
    assert service.calls == [(TOKEN, mode, 50, None)]


@pytest.mark.parametrize(
    "mode",
    ["af6e8f8c-cab7-4e4d-8ca5-5c564eed4728", "survival", "Classic"],
    ids=["a-uuid-no-longer-valid", "unknown-name", "wrong-case"],
)
async def test_a_game_mode_that_is_not_daily_classic_or_zen_is_422(mode):
    service = FakeLeaderboardService()

    response = await get(build_app(service), game_mode=mode)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "error",
    [
        NonDailyLeaderboardWithDate("x"),
        LeaderboardInvalidDate("x"),
    ],
    ids=["date-in-non-daily-mode", "invalid-date"],
)
async def test_domain_errors_are_422(error):
    response = await get(build_app(FakeLeaderboardService(error=error)))

    assert response.status_code == 422


async def test_requires_authentication():
    service = FakeLeaderboardService()

    response = await get(build_app(service, authenticated=False))

    assert response.status_code == 401
    assert service.calls == []
