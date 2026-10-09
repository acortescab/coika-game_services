import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID, DAILY_GAME_MODE_ID
from coika_game_service.api.db.models import MatchStatus
from tests.integration.conftest import headers, matches_of

pytestmark = pytest.mark.integration


def utc_today_seed() -> int:
    return int(datetime.now(UTC).strftime("%Y%m%d"))


async def start(client, keypair, player_id, mode_id, key=None):
    """POST /matches for a game mode."""
    return await client.post(
        "/matches",
        json={"game_mode_id": str(mode_id)},
        headers=headers(keypair, player_id, key or uuid.uuid4()),
    )


async def test_starting_a_daily_match_returns_201_with_the_seed_of_today(
    client, keypair, sessions, player
):
    before = utc_today_seed()
    response = await start(client, keypair, player, DAILY_GAME_MODE_ID)
    after = utc_today_seed()

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "in_progress"
    assert isinstance(body["seed"], int)
    assert body["seed"] in {before, after}  # either side of a midnight, if the test runs then
    [stored] = await matches_of(sessions, player)
    assert stored.seed == body["seed"]
    assert str(stored.id) == body["match_id"]


async def test_a_classic_match_returns_a_null_seed(client, keypair, sessions, player):
    response = await start(client, keypair, player, CLASSIC_GAME_MODE_ID)

    assert response.status_code == 201
    assert "seed" in response.json()
    assert response.json()["seed"] is None
    [stored] = await matches_of(sessions, player)
    assert stored.seed is None


async def test_a_retry_gets_exactly_the_same_response_including_the_seed(
    client, keypair, sessions, player
):
    key = uuid.uuid4()

    first = await start(client, keypair, player, DAILY_GAME_MODE_ID, key)
    retry = await start(client, keypair, player, DAILY_GAME_MODE_ID, key)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()
    assert len(await matches_of(sessions, player)) == 1


async def test_two_players_get_the_same_seed_on_the_same_day(
    client, keypair, player, other_player
):
    before = utc_today_seed()
    first = await start(client, keypair, player, DAILY_GAME_MODE_ID)
    second = await start(client, keypair, other_player, DAILY_GAME_MODE_ID)
    after = utc_today_seed()

    if before == after:  # skip the comparison only if the test straddles midnight
        assert first.json()["seed"] == second.json()["seed"] == before


async def test_zen_is_not_a_mode_of_the_server(client, keypair, sessions, player):
    """Zen has no matches in the server: any id that is not in the catalog is a 422."""
    response = await start(client, keypair, player, uuid.uuid4())

    assert response.status_code == 422
    assert await matches_of(sessions, player) == []


async def test_daily_retries_are_unlimited_and_leave_one_open_match(
    client, keypair, sessions, player
):
    responses = [await start(client, keypair, player, DAILY_GAME_MODE_ID) for _ in range(6)]

    assert [r.status_code for r in responses] == [201] * 6
    stored = await matches_of(sessions, player)
    assert len(stored) == 6
    assert sum(m.status == MatchStatus.IN_PROGRESS for m in stored) == 1
    assert sum(m.status == MatchStatus.ABANDONED for m in stored) == 5


async def test_concurrent_daily_starts_all_get_a_seed_and_leave_one_open_match(
    client, keypair, sessions, player
):
    responses = await asyncio.gather(
        *(start(client, keypair, player, DAILY_GAME_MODE_ID) for _ in range(6))
    )

    assert [r.status_code for r in responses] == [201] * 6
    assert len({r.json()["seed"] for r in responses}) <= 2  # two only if midnight passes
    stored = await matches_of(sessions, player)
    assert sum(m.status == MatchStatus.IN_PROGRESS for m in stored) == 1


async def test_the_same_key_for_another_mode_is_409(client, keypair, player):
    key = uuid.uuid4()
    await start(client, keypair, player, DAILY_GAME_MODE_ID, key)

    response = await start(client, keypair, player, CLASSIC_GAME_MODE_ID, key)

    assert response.status_code == 409
