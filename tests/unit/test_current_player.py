import time
from uuid import UUID

import httpx
import pytest

from coika_game_service.api.core.jwks import JWKSCache
from coika_game_service.api.dependencies import CurrentPlayer
from coika_game_service.main import create_app
from tests.auth_helpers import make_jwks, make_keypair, make_token

PLAYER_ID = UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")


@pytest.fixture
def keypair():
    return make_keypair()


def build_app(handler):
    """Real app (with its exception handlers) plus a protected route and a JWKS mock."""
    app = create_app()

    @app.get("/me")
    async def me(player_id: CurrentPlayer):
        return {"player_id": str(player_id)}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.jwks = JWKSCache("http://auth/jwks", 300, client)
    return app


async def get(app, headers=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/me", headers=headers)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app(keypair):
    return build_app(lambda _request: httpx.Response(200, json=make_jwks(keypair)))


async def test_valid_token_returns_the_player_id(app, keypair):
    response = await get(app, bearer(make_token(keypair)))

    assert response.status_code == 200
    assert response.json() == {"player_id": str(PLAYER_ID)}


async def test_missing_token_is_401_with_www_authenticate(app):
    response = await get(app)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer "},
    ],
    ids=["garbage-token", "wrong-scheme", "empty-token"],
)
async def test_invalid_token_is_401_with_www_authenticate(app, headers):
    response = await get(app, headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


async def test_expired_token_is_401(app, keypair):
    token = make_token(keypair, exp=int(time.time()) - 3600)

    response = await get(app, bearer(token))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


async def test_token_signed_by_another_key_is_401(app):
    response = await get(app, bearer(make_token(make_keypair())))

    assert response.status_code == 401


async def test_jwks_down_without_cache_is_503_not_401(keypair):
    app = build_app(lambda _request: httpx.Response(500))

    response = await get(app, bearer(make_token(keypair)))

    assert response.status_code == 503
    assert "WWW-Authenticate" not in response.headers


async def test_jwks_down_after_warm_cache_still_authenticates(keypair):
    state = {"down": False}

    def handler(_request):
        if state["down"]:
            return httpx.Response(500)
        return httpx.Response(200, json=make_jwks(keypair))

    app = build_app(handler)
    token = make_token(keypair)
    assert (await get(app, bearer(token))).status_code == 200

    state["down"] = True
    # Force a stale cache so the next request tries (and fails) to refresh.
    app.state.jwks._fetched_at = -1e9

    assert (await get(app, bearer(token))).status_code == 200

