import uuid
from types import SimpleNamespace

import httpx
import pytest

from coika_game_service.api.core.dependencies import current_player
from coika_game_service.api.core.factories import get_match_service
from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID, DAILY_GAME_MODE_ID
from coika_game_service.api.services.match_service import GameModeNotFound, IdempotencyKeyReused
from coika_game_service.main import create_app

PLAYER_ID = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
MATCH_ID = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")


def fake_match(seed=None):
    """The fields of a Match that the route reads."""
    return SimpleNamespace(id=MATCH_ID, status="in_progress", seed=seed)


class FakeMatchService:
    """Stands in for MatchService: records the call and returns or raises what the test sets."""

    def __init__(self, result=None, error=None):
        self.result = result or (fake_match(), True)
        self.error = error
        self.calls = []

    async def create_match(self, player_id, game_mode_id, idempotency_key):
        self.calls.append((player_id, game_mode_id, idempotency_key))
        if self.error:
            raise self.error
        return self.result


def build_app(service: FakeMatchService, *, authenticated: bool = True):
    app = create_app()
    app.dependency_overrides[get_match_service] = lambda: service
    if authenticated:
        app.dependency_overrides[current_player] = lambda: PLAYER_ID
    else:
        # No token is sent, so the key set is never consulted; it only has to exist.
        app.state.jwks = SimpleNamespace()
    return app


NO_BODY = object()


async def post(app, game_mode_id=CLASSIC_GAME_MODE_ID, headers=None, body=None) -> httpx.Response:
    """POST /matches with the game mode in the body, unless `body` replaces it (NO_BODY: none)."""
    if body is None:
        body = {"game_mode_id": str(game_mode_id)}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        if body is NO_BODY:
            return await client.post("/matches", headers=headers)
        return await client.post("/matches", headers=headers, json=body)


KEY = uuid.UUID("c0ffee00-0000-4000-8000-000000000001")


async def test_creates_a_match_with_201_and_passes_player_mode_and_key_to_the_service():
    service = FakeMatchService()

    response = await post(build_app(service), headers={"Idempotency-Key": str(KEY)})

    assert response.status_code == 201
    assert response.json() == {"match_id": str(MATCH_ID), "status": "in_progress", "seed": None}
    assert service.calls == [(PLAYER_ID, CLASSIC_GAME_MODE_ID, KEY)]


async def test_retry_with_the_same_key_gets_the_same_201_response():
    service = FakeMatchService(result=(fake_match(seed=20261010), False))

    response = await post(build_app(service), headers={"Idempotency-Key": str(KEY)})

    assert response.status_code == 201
    assert response.json() == {
        "match_id": str(MATCH_ID), "status": "in_progress", "seed": 20261010,
    }


async def test_the_response_carries_the_seed_of_the_daily_mode():
    service = FakeMatchService(result=(fake_match(seed=20261010), True))

    response = await post(
        build_app(service), game_mode_id=DAILY_GAME_MODE_ID, headers={"Idempotency-Key": str(KEY)}
    )

    assert response.status_code == 201
    assert response.json()["seed"] == 20261010
    assert service.calls == [(PLAYER_ID, DAILY_GAME_MODE_ID, KEY)]


async def test_the_seed_is_null_in_the_response_when_the_mode_has_none():
    response = await post(build_app(FakeMatchService()), headers={"Idempotency-Key": str(KEY)})

    assert "seed" in response.json()
    assert response.json()["seed"] is None


async def test_missing_idempotency_key_is_422():
    service = FakeMatchService()

    response = await post(build_app(service))

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("value", ["not-a-uuid", "123", "", "c0ffee00-0000-4000-8000"])
async def test_invalid_idempotency_key_is_422(value):
    service = FakeMatchService()

    response = await post(build_app(service), headers={"Idempotency-Key": value})

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [NO_BODY, {}, {"game_mode_id": "classic"}, {"game_mode_id": 5}, {"game_mode_id": None}],
    ids=["no-body", "empty-object", "not-a-uuid", "number", "null"],
)
async def test_missing_or_invalid_game_mode_id_in_the_body_is_422(body):
    service = FakeMatchService()

    response = await post(build_app(service), headers={"Idempotency-Key": str(KEY)}, body=body)

    assert response.status_code == 422
    assert service.calls == []


async def test_unknown_game_mode_is_422():
    service = FakeMatchService(error=GameModeNotFound("x"))

    response = await post(build_app(service), headers={"Idempotency-Key": str(KEY)})

    assert response.status_code == 422
    assert response.json() == {"detail": "Game mode not found"}


async def test_key_reused_for_another_game_mode_is_409():
    service = FakeMatchService(error=IdempotencyKeyReused("x"))

    response = await post(build_app(service), headers={"Idempotency-Key": str(KEY)})

    assert response.status_code == 409


async def test_requires_authentication():
    service = FakeMatchService()

    response = await post(
        build_app(service, authenticated=False), headers={"Idempotency-Key": str(KEY)}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert service.calls == []
