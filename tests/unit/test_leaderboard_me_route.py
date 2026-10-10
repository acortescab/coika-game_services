"""HU-09: GET /game-modes/{game_mode}/leaderboard/me?around=N&date=yyyyMMdd (route contract)."""
import uuid
from types import SimpleNamespace

import httpx
import pytest

from coika_game_service.api.core.dependencies import bearer_token, current_player
from coika_game_service.api.core.exceptions import (
    InvalidLeaderboardRank,
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.main import create_app

TOKEN = "caller-access-token"
ME = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
ABOVE = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")
BELOW = uuid.UUID("7a1c2d3e-4f50-4a61-8b72-9c0d1e2f3a4b")


class FakeLeaderboardService:
    """Stands in for LeaderboardService: records the call and returns or raises what is set."""

    def __init__(self, result=None, error=None):
        self.result = result if result is not None else []
        self.error = error
        self.calls = []

    async def get_leaderboard_me(self, token, player_id, game_mode, around, date):
        self.calls.append((token, player_id, game_mode, around, date))
        if self.error:
            raise self.error
        return self.result


def build_app(service, *, authenticated=True):
    app = create_app()
    app.dependency_overrides[get_leaderboard_service] = lambda: service
    if authenticated:
        app.dependency_overrides[bearer_token] = lambda: TOKEN
        app.dependency_overrides[current_player] = lambda: ME
    else:
        # No token is sent, so the key set is never consulted; it only has to exist.
        app.state.jwks = SimpleNamespace()
    return app


async def get(app, game_mode="classic", **params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(f"/game-modes/{game_mode}/leaderboard/me", params=params)


# --- Criterion 1: my rank, my score and N neighbours above and below -------------------------


async def test_returns_me_and_my_neighbours_with_position_name_and_score():
    service = FakeLeaderboardService([
        LeaderboardResponse(position=41, player_id=str(ABOVE), name="Above", score=900),
        LeaderboardResponse(position=42, player_id=str(ME), name="Me", score=800),
        LeaderboardResponse(position=43, player_id=str(BELOW), name="Below", score=700),
    ])

    response = await get(build_app(service), around=1)

    assert response.status_code == 200
    assert response.json() == [
        {"position": 41, "player_id": str(ABOVE), "name": "Above", "score": 900},
        {"position": 42, "player_id": str(ME), "name": "Me", "score": 800},
        {"position": 43, "player_id": str(BELOW), "name": "Below", "score": 700},
    ]


async def test_around_reaches_the_service_with_the_caller_the_token_and_the_mode():
    service = FakeLeaderboardService()

    await get(build_app(service), around=7)

    assert service.calls == [(TOKEN, ME, GameModeName.CLASSIC, 7, None)]


async def test_the_player_is_the_authenticated_one_never_a_parameter():
    """Asking for another player's surroundings must not be possible."""
    service = FakeLeaderboardService()

    await get(build_app(service), around=3, player_id=str(BELOW))

    assert service.calls[0][1] == ME


async def test_around_has_a_default():
    service = FakeLeaderboardService()

    await get(build_app(service))

    assert service.calls[0][3] == 25


@pytest.mark.parametrize("params", [{"around": 0}, {"around": 51}, {"around": "many"}],
                         ids=["zero", "over-the-maximum", "not-a-number"])
async def test_an_invalid_around_is_422_and_the_service_is_not_called(params):
    service = FakeLeaderboardService()

    response = await get(build_app(service), **params)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("mode", list(GameModeName))
async def test_every_game_mode_name_is_accepted(mode):
    service = FakeLeaderboardService()

    response = await get(build_app(service), game_mode=mode.value)

    assert response.status_code == 200
    assert service.calls[0][2] == mode


# --- Criterion 2: date with the same meaning and rules as HU-08 ------------------------------


async def test_the_date_reaches_the_service():
    service = FakeLeaderboardService()

    await get(build_app(service), game_mode="daily", around=5, date="20261009")

    assert service.calls == [(TOKEN, ME, GameModeName.DAILY, 5, "20261009")]


@pytest.mark.parametrize("date", ["2026-10-09", "2026100", "abcdefgh", "202610099"],
                         ids=["dashes", "7-digits", "letters", "9-digits"])
async def test_a_date_that_is_not_yyyymmdd_is_422(date):
    service = FakeLeaderboardService()

    response = await get(build_app(service), game_mode="daily", date=date)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "error",
    [NonDailyLeaderboardWithDate("x"), LeaderboardInvalidDate("x")],
    ids=["date-in-non-daily-mode", "not-a-real-date"],
)
async def test_domain_date_errors_are_422(error):
    response = await get(build_app(FakeLeaderboardService(error=error)), date="20261009")

    assert response.status_code == 422


# --- Criterion 4: no score yet -> 404 with a clear message -----------------------------------


@pytest.mark.parametrize("mode", ["classic", "zen", "daily"])
async def test_a_player_without_score_is_404_with_a_clear_message(mode):
    service = FakeLeaderboardService(error=InvalidLeaderboardRank("no score"))

    response = await get(build_app(service), game_mode=mode)

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found in the leaderboard"}


async def test_a_player_without_score_on_that_date_is_404():
    service = FakeLeaderboardService(error=InvalidLeaderboardRank("no score that day"))

    response = await get(build_app(service), game_mode="daily", date="20200101")

    assert response.status_code == 404


async def test_requires_authentication():
    service = FakeLeaderboardService()

    response = await get(build_app(service, authenticated=False))

    assert response.status_code == 401
    assert service.calls == []
