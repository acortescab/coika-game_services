import asyncio
import uuid

import pytest

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.db.models import MatchStatus, RejectReason
from tests.auth_helpers import make_keypair, make_token
from tests.integration.conftest import age_match, headers, matches_of, scores_of

pytestmark = pytest.mark.integration

FIGURES = {"score": 1500, "pieces_dropped": 120, "highest_tier": 7}


async def start_match(client, keypair, player_id, sessions, age=60) -> str:
    """POST /matches and returns the id of the new match."""
    response = await client.post(
        "/matches",
        json={"game_mode_id": str(CLASSIC_GAME_MODE_ID)},
        headers=headers(keypair, player_id, uuid.uuid4()),
    )
    assert response.status_code == 201, response.text
    match_id = response.json()["match_id"]
    # The anti-cheat rules measure the duration from started_at: make it a 60 s match
    await age_match(sessions, match_id, age)
    return match_id


async def send_score(client, keypair, player_id, match_id, figures=FIGURES):
    """POST /matches/{match_id}/score."""
    return await client.post(
        f"/matches/{match_id}/score", json=figures, headers=headers(keypair, player_id)
    )


async def test_the_whole_flow_start_a_match_then_submit_its_score(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)

    response = await send_score(client, keypair, player, match_id)

    assert response.status_code == 201
    body = response.json()
    assert body["match_id"] == match_id
    assert (body["score"], body["pieces_dropped"], body["highest_tier"]) == (1500, 120, 7)
    assert body["created_at"]
    [stored_score] = await scores_of(sessions, player)
    assert (stored_score.score, stored_score.pieces_dropped, stored_score.highest_tier) == (
        1500, 120, 7,
    )
    [match] = await matches_of(sessions, player)
    assert match.status == MatchStatus.FINISHED
    assert match.finish_at is not None


async def test_retry_gets_exactly_the_same_201_response(client, keypair, sessions, player):
    match_id = await start_match(client, keypair, player, sessions)

    first = await send_score(client, keypair, player, match_id)
    retry = await send_score(client, keypair, player, match_id)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()
    assert len(await scores_of(sessions, player)) == 1


async def test_other_figures_for_a_finished_match_are_409_and_change_nothing(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)
    await send_score(client, keypair, player, match_id)

    response = await send_score(
        client, keypair, player, match_id, {**FIGURES, "score": 99_999}
    )

    assert response.status_code == 409
    [stored] = await scores_of(sessions, player)
    assert stored.score == 1500


async def test_the_match_of_another_player_is_404_and_nothing_changes(
    client, keypair, sessions, player, other_player
):
    match_id = await start_match(client, keypair, player, sessions)

    response = await send_score(client, keypair, other_player, match_id)

    assert response.status_code == 404
    assert await scores_of(sessions, player) == []
    [match] = await matches_of(sessions, player)
    assert match.status == MatchStatus.IN_PROGRESS


async def test_an_unknown_match_is_404(client, keypair, player):
    response = await send_score(client, keypair, player, uuid.uuid4())

    assert response.status_code == 404


async def test_an_abandoned_match_is_409(client, keypair, sessions, player):
    abandoned = await start_match(client, keypair, player, sessions)
    await start_match(client, keypair, player, sessions)  # a new match abandons the previous one

    response = await send_score(client, keypair, player, abandoned)

    assert response.status_code == 409
    assert await scores_of(sessions, player) == []


async def test_after_submitting_a_new_match_can_start_and_the_finished_one_stays_finished(
    client, keypair, sessions, player
):
    first = await start_match(client, keypair, player, sessions)
    await send_score(client, keypair, player, first)

    second = await start_match(client, keypair, player, sessions)

    by_id = {str(m.id): m for m in await matches_of(sessions, player)}
    assert by_id[first].status == MatchStatus.FINISHED
    assert by_id[second].status == MatchStatus.IN_PROGRESS


@pytest.mark.parametrize(
    "figures",
    [
        {**FIGURES, "score": -1},
        {**FIGURES, "pieces_dropped": -1},
        {**FIGURES, "highest_tier": 11},
        {**FIGURES, "highest_tier": -1},
        {**FIGURES, "score": "lots"},
        {"pieces_dropped": 1, "highest_tier": 1},
        {},
    ],
    ids=["negative-score", "negative-pieces", "tier-above-10", "negative-tier",
         "not-a-number", "missing-score", "empty"],
)
async def test_invalid_figures_are_422_and_the_match_stays_open(
    client, keypair, sessions, player, figures
):
    match_id = await start_match(client, keypair, player, sessions)

    response = await send_score(client, keypair, player, match_id, figures)

    assert response.status_code == 422
    assert await scores_of(sessions, player) == []
    [match] = await matches_of(sessions, player)
    assert match.status == MatchStatus.IN_PROGRESS


async def test_the_match_id_in_the_url_must_be_a_uuid(client, keypair, player):
    response = await send_score(client, keypair, player, "not-a-uuid")

    assert response.status_code == 422


async def test_without_a_valid_token_the_request_is_401_and_nothing_is_stored(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)
    forged = {"Authorization": f"Bearer {make_token(make_keypair(), sub=str(player))}"}

    no_token = await client.post(f"/matches/{match_id}/score", json=FIGURES)
    bad_token = await client.post(f"/matches/{match_id}/score", json=FIGURES, headers=forged)

    assert no_token.status_code == 401
    assert bad_token.status_code == 401
    assert bad_token.headers["WWW-Authenticate"].startswith("Bearer")
    assert await scores_of(sessions, player) == []


async def test_concurrent_identical_submissions_all_get_the_same_201(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)

    responses = await asyncio.gather(
        *(send_score(client, keypair, player, match_id) for _ in range(8))
    )

    assert [r.status_code for r in responses] == [201] * 8
    assert len({r.text for r in responses}) == 1
    assert len(await scores_of(sessions, player)) == 1


async def test_concurrent_different_submissions_store_one_and_refuse_the_rest(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)

    responses = await asyncio.gather(
        *(
            send_score(client, keypair, player, match_id, {**FIGURES, "score": 1500 + n})
            for n in range(8)
        )
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 7
    [stored] = await scores_of(sessions, player)
    winner = next(r for r in responses if r.status_code == 201)
    assert winner.json()["score"] == stored.score


@pytest.mark.parametrize(
    ("figures", "age", "reason"),
    [
        ({**FIGURES, "score": 2_000_000}, 60, RejectReason.SCORE_MAX),
        ({**FIGURES, "score": 0, "highest_tier": 0}, 60, RejectReason.SCORE_MIN),
        ({**FIGURES, "pieces_dropped": 5000}, 60, RejectReason.PIECES),
        ({**FIGURES, "score": 100_000}, 60, RejectReason.SCORE_RATE),
        ({**FIGURES, "score": 100, "highest_tier": 10}, 60, RejectReason.SCORE_TIER),
        (FIGURES, 7200, RejectReason.DURATION),
        ({"score": 5, "pieces_dropped": 1, "highest_tier": 1}, 1, RejectReason.DURATION_MIN),
    ],
    ids=["score-max", "score-min", "pieces", "score-rate", "score-tier", "too-long", "too-short"],
)
async def test_an_impossible_score_is_422_with_the_reason_and_the_match_is_rejected(
    client, keypair, sessions, player, figures, age, reason
):
    match_id = await start_match(client, keypair, player, sessions, age=age)

    response = await send_score(client, keypair, player, match_id, figures)

    assert response.status_code == 422
    assert reason.value in response.json()["detail"]
    assert await scores_of(sessions, player) == []
    [match] = await matches_of(sessions, player)
    assert match.status == MatchStatus.REJECTED
    assert match.rejection_reason == reason
    assert match.finish_at is not None


async def test_a_rejected_match_accepts_no_other_score(client, keypair, sessions, player):
    match_id = await start_match(client, keypair, player, sessions)
    await send_score(client, keypair, player, match_id, {**FIGURES, "score": 2_000_000})

    response = await send_score(client, keypair, player, match_id)

    assert response.status_code == 409
    assert await scores_of(sessions, player) == []


async def test_a_rejected_match_lets_the_player_start_another_one(
    client, keypair, sessions, player
):
    match_id = await start_match(client, keypair, player, sessions)
    await send_score(client, keypair, player, match_id, {**FIGURES, "score": 2_000_000})

    second = await start_match(client, keypair, player, sessions)

    assert (await send_score(client, keypair, player, second)).status_code == 201
