import asyncio
import uuid

import pytest

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.db.models import MatchStatus
from tests.auth_helpers import make_keypair, make_token
from tests.integration.conftest import headers, matches_of

pytestmark = pytest.mark.integration


def body(game_mode_id=CLASSIC_GAME_MODE_ID):
    """The game mode travels in the body of POST /matches."""
    return {"game_mode_id": str(game_mode_id)}


async def post_match(client, request_headers, game_mode_id=CLASSIC_GAME_MODE_ID):
    """POST /matches for a game mode."""
    return await client.post("/matches", json=body(game_mode_id), headers=request_headers)


async def test_starting_a_match_returns_201_and_stores_it_in_progress(
    client, keypair, sessions, player
):
    response = await post_match(client, headers(keypair, player, uuid.uuid4()))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "in_progress"
    stored = await matches_of(sessions, player)
    assert [str(m.id) for m in stored] == [body["match_id"]]
    assert stored[0].player_id == player  # the player comes from the token, not from the request
    assert stored[0].game_mode_id == CLASSIC_GAME_MODE_ID
    assert stored[0].started_at is not None
    assert stored[0].finish_at is None


async def test_retry_with_the_same_key_gets_the_same_201_response(
    client, keypair, sessions, player
):
    request_headers = headers(keypair, player, uuid.uuid4())

    first = await post_match(client, request_headers)
    retry = await post_match(client, request_headers)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()
    assert len(await matches_of(sessions, player)) == 1


async def test_same_key_for_another_game_mode_is_409(client, keypair, player, other_mode):
    request_headers = headers(keypair, player, uuid.uuid4())
    await post_match(client, request_headers)

    response = await post_match(client, request_headers, other_mode)

    assert response.status_code == 409


async def test_idempotency_key_is_required_and_must_be_a_uuid(client, keypair, sessions, player):
    missing = await post_match(client, headers(keypair, player))
    invalid = await post_match(client, headers(keypair, player, "not-a-uuid"))

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert await matches_of(sessions, player) == []


async def test_without_a_valid_token_the_request_is_401_and_nothing_is_created(
    client, sessions, player
):
    key = {"Idempotency-Key": str(uuid.uuid4())}

    no_token = await post_match(client, key)
    forged = await client.post(
        "/matches",
        json=body(),
        headers={**key, "Authorization": f"Bearer {make_token(make_keypair(), sub=str(player))}"},
    )

    assert no_token.status_code == 401
    assert forged.status_code == 401
    assert forged.headers["WWW-Authenticate"].startswith("Bearer")
    assert await matches_of(sessions, player) == []


async def test_unknown_game_mode_is_422_and_nothing_is_created(client, keypair, sessions, player):
    response = await client.post(
        "/matches",
        json=body(uuid.uuid4()),
        headers=headers(keypair, player, uuid.uuid4()),
    )

    assert response.status_code == 422
    assert await matches_of(sessions, player) == []


@pytest.mark.parametrize(
    "payload", [None, {}, {"game_mode_id": "classic"}], ids=["no-body", "empty", "not-a-uuid"]
)
async def test_missing_or_invalid_game_mode_in_the_body_is_422_and_creates_nothing(
    client, keypair, sessions, player, payload
):
    response = await client.post(
        "/matches", json=payload, headers=headers(keypair, player, uuid.uuid4())
    )

    assert response.status_code == 422
    assert await matches_of(sessions, player) == []


async def test_starting_another_match_abandons_the_previous_one(client, keypair, sessions, player):
    first = await post_match(client, headers(keypair, player, uuid.uuid4()))
    second = await post_match(client, headers(keypair, player, uuid.uuid4()))

    assert (first.status_code, second.status_code) == (201, 201)
    by_id = {str(m.id): m for m in await matches_of(sessions, player)}
    assert by_id[first.json()["match_id"]].status == MatchStatus.ABANDONED
    assert by_id[first.json()["match_id"]].finish_at is not None
    assert by_id[second.json()["match_id"]].status == MatchStatus.IN_PROGRESS


async def test_concurrent_requests_leave_a_single_open_match(client, keypair, sessions, player):
    responses = await asyncio.gather(
        *(post_match(client, headers(keypair, player, uuid.uuid4())) for _ in range(8))
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
        *(post_match(client, request_headers) for _ in range(8))
    )

    assert [r.status_code for r in responses] == [201] * 8
    assert len({r.json()["match_id"] for r in responses}) == 1
    assert len(await matches_of(sessions, player)) == 1
