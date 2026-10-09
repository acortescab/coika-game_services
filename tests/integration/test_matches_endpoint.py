import asyncio
import uuid

import httpx
import pytest

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.core.jwks import JWKSCache
from coika_game_service.api.db.models import MatchStatus
from coika_game_service.main import create_app
from tests.auth_helpers import make_jwks, make_keypair, make_token
from tests.integration.conftest import matches_of

pytestmark = pytest.mark.integration


@pytest.fixture
def keypair():
    return make_keypair()


@pytest.fixture
async def client(sessions, keypair):
    """
    The real app (lifespan, routes, services, Postgres) called over HTTP. Only the auth
    service is simulated: its JWKS is served from memory and the tokens are really signed.
    """
    app = create_app()
    async with app.router.lifespan_context(app):
        mock_auth = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=make_jwks(keypair)))
        )
        app.state.jwks = JWKSCache("http://auth/jwks", 300, mock_auth)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def headers(keypair, player_id, key=None):
    result = {"Authorization": f"Bearer {make_token(keypair, sub=str(player_id))}"}
    if key is not None:
        result["Idempotency-Key"] = str(key)
    return result


def url(game_mode_id=CLASSIC_GAME_MODE_ID):
    return f"/game-modes/{game_mode_id}/matches"


async def test_starting_a_match_returns_201_and_stores_it_in_progress(
    client, keypair, sessions, player
):
    response = await client.post(url(), headers=headers(keypair, player, uuid.uuid4()))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "in_progress"
    stored = await matches_of(sessions, player)
    assert [str(m.id) for m in stored] == [body["match_id"]]
    assert stored[0].player_id == player  # the player comes from the token, not from the request
    assert stored[0].game_mode_id == CLASSIC_GAME_MODE_ID
    assert stored[0].started_at is not None
    assert stored[0].finish_at is None


async def test_retry_with_the_same_key_returns_200_and_the_same_match(
    client, keypair, sessions, player
):
    request_headers = headers(keypair, player, uuid.uuid4())

    first = await client.post(url(), headers=request_headers)
    retry = await client.post(url(), headers=request_headers)

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert len(await matches_of(sessions, player)) == 1


async def test_same_key_for_another_game_mode_is_409(client, keypair, player, other_mode):
    request_headers = headers(keypair, player, uuid.uuid4())
    await client.post(url(), headers=request_headers)

    response = await client.post(url(other_mode), headers=request_headers)

    assert response.status_code == 409


async def test_idempotency_key_is_required_and_must_be_a_uuid(client, keypair, sessions, player):
    missing = await client.post(url(), headers=headers(keypair, player))
    invalid = await client.post(url(), headers=headers(keypair, player, "not-a-uuid"))

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert await matches_of(sessions, player) == []


async def test_without_a_valid_token_the_request_is_401_and_nothing_is_created(
    client, sessions, player
):
    key = {"Idempotency-Key": str(uuid.uuid4())}

    no_token = await client.post(url(), headers=key)
    forged = await client.post(
        url(),
        headers={**key, "Authorization": f"Bearer {make_token(make_keypair(), sub=str(player))}"},
    )

    assert no_token.status_code == 401
    assert forged.status_code == 401
    assert forged.headers["WWW-Authenticate"].startswith("Bearer")
    assert await matches_of(sessions, player) == []


async def test_unknown_game_mode_is_404_and_nothing_is_created(client, keypair, sessions, player):
    response = await client.post(
        url(uuid.uuid4()), headers=headers(keypair, player, uuid.uuid4())
    )

    assert response.status_code == 404
    assert await matches_of(sessions, player) == []


async def test_starting_another_match_abandons_the_previous_one(client, keypair, sessions, player):
    first = await client.post(url(), headers=headers(keypair, player, uuid.uuid4()))
    second = await client.post(url(), headers=headers(keypair, player, uuid.uuid4()))

    assert (first.status_code, second.status_code) == (201, 201)
    by_id = {str(m.id): m for m in await matches_of(sessions, player)}
    assert by_id[first.json()["match_id"]].status == MatchStatus.ABANDONED
    assert by_id[first.json()["match_id"]].finish_at is not None
    assert by_id[second.json()["match_id"]].status == MatchStatus.IN_PROGRESS


async def test_concurrent_requests_leave_a_single_open_match(client, keypair, sessions, player):
    responses = await asyncio.gather(
        *(client.post(url(), headers=headers(keypair, player, uuid.uuid4())) for _ in range(8))
    )

    assert [r.status_code for r in responses] == [201] * 8
    stored = await matches_of(sessions, player)
    assert len(stored) == 8
    assert sum(m.status == MatchStatus.IN_PROGRESS for m in stored) == 1


async def test_concurrent_retries_of_the_same_request_create_one_match(
    client, keypair, sessions, player
):
    request_headers = headers(keypair, player, uuid.uuid4())

    responses = await asyncio.gather(
        *(client.post(url(), headers=request_headers) for _ in range(8))
    )

    assert sorted(r.status_code for r in responses) == [200] * 7 + [201]
    assert len({r.json()["match_id"] for r in responses}) == 1
    assert len(await matches_of(sessions, player)) == 1
