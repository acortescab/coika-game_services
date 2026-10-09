import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from coika_game_service.api.dependencies import current_player, get_score_service
from coika_game_service.api.services.score_service import (
    MatchNotFound,
    MatchNotOpen,
    ScoreAlreadyExists,
)
from coika_game_service.main import create_app

PLAYER_ID = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
MATCH_ID = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")
CREATED_AT = datetime(2026, 10, 9, 21, 0, 0, tzinfo=UTC)
VALID = {"score": 1500, "pieces_dropped": 120, "highest_tier": 7}


class FakeScoreService:
    """Stands in for ScoreService: records the call and returns or raises what the test sets."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def create_score(self, player_id, match_id, payload):
        self.calls.append((player_id, match_id, payload))
        if self.error:
            raise self.error
        return SimpleNamespace(
            match_id=match_id,
            score=payload.score,
            pieces_dropped=payload.pieces_dropped,
            highest_tier=payload.highest_tier,
            created_at=CREATED_AT,
        )


def build_app(service: FakeScoreService, *, authenticated: bool = True):
    app = create_app()
    app.dependency_overrides[get_score_service] = lambda: service
    if authenticated:
        app.dependency_overrides[current_player] = lambda: PLAYER_ID
    else:
        # No token is sent, so the key set is never consulted; it only has to exist.
        app.state.jwks = SimpleNamespace()
    return app


async def post(app, match_id=MATCH_ID, json=VALID) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(f"/matches/{match_id}/score", json=json)


async def test_submitting_a_score_returns_201_with_the_stored_figures():
    service = FakeScoreService()

    response = await post(build_app(service))

    assert response.status_code == 201
    assert response.json() == {
        "match_id": str(MATCH_ID),
        "score": 1500,
        "pieces_dropped": 120,
        "highest_tier": 7,
        "created_at": "2026-10-09T21:00:00Z",
    }


async def test_the_player_comes_from_the_token_and_the_match_from_the_url():
    service = FakeScoreService()

    await post(build_app(service))

    [(player_id, match_id, payload)] = service.calls
    assert player_id == PLAYER_ID
    assert match_id == MATCH_ID
    assert (payload.score, payload.pieces_dropped, payload.highest_tier) == (1500, 120, 7)


@pytest.mark.parametrize(
    "json",
    [
        {**VALID, "score": -1},
        {**VALID, "pieces_dropped": -1},
        {**VALID, "highest_tier": -1},
        {**VALID, "highest_tier": 11},
        {**VALID, "score": 2_147_483_648},
        {**VALID, "score": "lots"},
        {**VALID, "score": 1.5},
        {"pieces_dropped": 1, "highest_tier": 1},
        {"score": 1, "highest_tier": 1},
        {"score": 1, "pieces_dropped": 1},
        {},
    ],
    ids=[
        "negative-score",
        "negative-pieces",
        "negative-tier",
        "tier-above-max",
        "score-above-int32",
        "score-not-a-number",
        "score-with-decimals",
        "missing-score",
        "missing-pieces",
        "missing-tier",
        "empty-object",
    ],
)
async def test_invalid_body_is_422_and_the_service_is_not_called(json):
    service = FakeScoreService()

    response = await post(build_app(service), json=json)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("tier", [0, 10])
async def test_the_tier_limits_are_accepted(tier):
    response = await post(build_app(FakeScoreService()), json={**VALID, "highest_tier": tier})

    assert response.status_code == 201


async def test_a_score_of_zero_is_accepted():
    response = await post(build_app(FakeScoreService()), json={**VALID, "score": 0})

    assert response.status_code == 201


async def test_match_id_in_the_url_must_be_a_uuid():
    service = FakeScoreService()

    response = await post(build_app(service), match_id="not-a-uuid")

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MatchNotFound("x"), 404),
        (MatchNotOpen("x"), 409),
        (ScoreAlreadyExists("x"), 409),
    ],
    ids=["match-not-found", "match-not-open", "score-already-exists"],
)
async def test_domain_errors_map_to_their_status(error, status):
    response = await post(build_app(FakeScoreService(error=error)))

    assert response.status_code == status


async def test_requires_authentication():
    service = FakeScoreService()

    response = await post(build_app(service, authenticated=False))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert service.calls == []
