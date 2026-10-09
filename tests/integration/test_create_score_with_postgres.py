import asyncio
import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.db.models import Match, MatchStatus, Score
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services.match_service import MatchService
from coika_game_service.api.services.score_service import (
    MatchNotFound,
    MatchNotOpen,
    ScoreAlreadyExists,
    ScoreService,
)
from tests.integration.conftest import matches_of, scores_of

pytestmark = pytest.mark.integration


async def start_match(sessions, player_id) -> Match:
    """Starts a match like a request would: its own session, a new idempotency key."""
    async with sessions() as session:
        match, _ = await MatchService(MatchRepository(session, session), session).create_match(
            player_id, CLASSIC_GAME_MODE_ID, uuid.uuid4()
        )
        return match


async def submit(sessions, player_id, match_id, score=1500, pieces=120, tier=7) -> Score:
    """Submits a score like a request would: its own session."""
    payload = CreateScoreRequest(score=score, pieces_dropped=pieces, highest_tier=tier)
    async with sessions() as session:
        service = ScoreService(
            ScoreRepository(session, session), MatchRepository(session, session), session
        )
        return await service.create_score(player_id, match_id, payload)


async def match_by_id(sessions, player_id, match_id) -> Match:
    return next(m for m in await matches_of(sessions, player_id) if m.id == match_id)


async def test_submitting_stores_the_score_and_finishes_the_match(sessions, player):
    match = await start_match(sessions, player)

    score = await submit(sessions, player, match.id)

    assert (score.match_id, score.score, score.pieces_dropped, score.highest_tier) == (
        match.id, 1500, 120, 7,
    )
    [stored] = await scores_of(sessions, player)
    assert (stored.score, stored.pieces_dropped, stored.highest_tier) == (1500, 120, 7)
    finished = await match_by_id(sessions, player, match.id)
    assert finished.status == MatchStatus.FINISHED
    assert finished.finish_at is not None


async def test_retry_with_the_same_figures_returns_the_stored_score(sessions, player):
    match = await start_match(sessions, player)
    first = await submit(sessions, player, match.id)

    retry = await submit(sessions, player, match.id)

    assert retry.created_at == first.created_at
    assert len(await scores_of(sessions, player)) == 1


async def test_other_figures_for_a_finished_match_are_refused_and_change_nothing(
    sessions, player
):
    match = await start_match(sessions, player)
    await submit(sessions, player, match.id, score=1500)

    with pytest.raises(ScoreAlreadyExists):
        await submit(sessions, player, match.id, score=9999)

    [stored] = await scores_of(sessions, player)
    assert stored.score == 1500


@pytest.mark.parametrize(
    "changed", [{"score": 1}, {"pieces": 1}, {"tier": 1}], ids=["score", "pieces", "tier"]
)
async def test_any_different_figure_is_not_a_retry(sessions, player, changed):
    match = await start_match(sessions, player)
    await submit(sessions, player, match.id)

    with pytest.raises(ScoreAlreadyExists):
        await submit(sessions, player, match.id, **changed)


async def test_the_match_of_another_player_is_not_found_and_stays_open(
    sessions, player, other_player
):
    match = await start_match(sessions, player)

    with pytest.raises(MatchNotFound):
        await submit(sessions, other_player, match.id)

    assert await scores_of(sessions, player) == []
    assert (await match_by_id(sessions, player, match.id)).status == MatchStatus.IN_PROGRESS


async def test_an_unknown_match_is_not_found(sessions, player):
    with pytest.raises(MatchNotFound):
        await submit(sessions, player, uuid.uuid4())


async def test_an_abandoned_match_accepts_no_score(sessions, player):
    abandoned = await start_match(sessions, player)
    await start_match(sessions, player)  # starting another one abandons the first

    with pytest.raises(MatchNotOpen):
        await submit(sessions, player, abandoned.id)

    assert await scores_of(sessions, player) == []


async def test_a_rejected_match_accepts_no_score(sessions, player):
    match = await start_match(sessions, player)
    async with sessions() as session:
        await session.execute(
            update(Match).where(Match.id == match.id).values(status=MatchStatus.REJECTED)
        )
        await session.commit()

    with pytest.raises(MatchNotOpen):
        await submit(sessions, player, match.id)

    assert await scores_of(sessions, player) == []


async def test_submitting_does_not_touch_the_other_matches(sessions, player):
    first = await start_match(sessions, player)
    await submit(sessions, player, first.id)

    second = await start_match(sessions, player)

    assert (await match_by_id(sessions, player, first.id)).status == MatchStatus.FINISHED
    assert (await match_by_id(sessions, player, second.id)).status == MatchStatus.IN_PROGRESS


async def test_concurrent_identical_submissions_store_one_score(sessions, player):
    match = await start_match(sessions, player)

    results = await asyncio.gather(*(submit(sessions, player, match.id) for _ in range(10)))

    assert len({score.created_at for score in results}) == 1
    assert len(await scores_of(sessions, player)) == 1


async def test_concurrent_different_submissions_store_exactly_one(sessions, player):
    match = await start_match(sessions, player)

    outcomes = await asyncio.gather(
        *(submit(sessions, player, match.id, score=n) for n in range(10)),
        return_exceptions=True,
    )

    winners = [o for o in outcomes if isinstance(o, Score)]
    losers = [o for o in outcomes if isinstance(o, ScoreAlreadyExists)]
    assert len(winners) == 1
    assert len(losers) == 9
    [stored] = await scores_of(sessions, player)
    assert stored.score == winners[0].score


@pytest.mark.parametrize("attempt", range(5))
async def test_a_submission_racing_with_a_new_match_never_loses_or_duplicates_anything(
    sessions, player, attempt
):
    """
    The score of match A and the start of match B arrive together. Either the score wins
    (A is finished, B is open) or B abandons A first (the score is refused). Never both, and
    never two open matches.
    """
    first = await start_match(sessions, player)

    submitted, started = await asyncio.gather(
        submit(sessions, player, first.id),
        start_match(sessions, player),
        return_exceptions=True,
    )

    assert isinstance(started, Match), started
    stored_scores = await scores_of(sessions, player)
    first_now = await match_by_id(sessions, player, first.id)
    if isinstance(submitted, Score):
        assert first_now.status == MatchStatus.FINISHED
        assert len(stored_scores) == 1
    else:
        assert isinstance(submitted, MatchNotOpen), submitted
        assert first_now.status == MatchStatus.ABANDONED
        assert stored_scores == []
    open_matches = [
        m for m in await matches_of(sessions, player) if m.status == MatchStatus.IN_PROGRESS
    ]
    assert [m.id for m in open_matches] == [started.id]


async def test_the_match_row_stays_locked_while_a_submission_is_in_progress(sessions, player):
    """A second submission waits for the first one instead of racing it."""
    match = await start_match(sessions, player)

    async with sessions() as holder:
        await MatchRepository(holder, holder).get_for_update(match.id)  # lock, no commit yet
        waiting = asyncio.ensure_future(submit(sessions, player, match.id))
        await asyncio.sleep(0.3)
        assert not waiting.done()

        await holder.rollback()  # releases the lock
        assert (await asyncio.wait_for(waiting, timeout=5)).match_id == match.id


@pytest.mark.parametrize(
    ("figures", "constraint"),
    [
        ((-1, 10, 3), "ck_scores_score_non_negative"),
        ((10, -1, 3), "ck_scores_pieces_dropped_non_negative"),
        ((10, 10, -1), "ck_scores_highest_tier_valid"),
        ((10, 10, 11), "ck_scores_highest_tier_valid"),
    ],
    ids=["negative-score", "negative-pieces", "negative-tier", "tier-above-10"],
)
async def test_the_database_refuses_invalid_figures(sessions, player, figures, constraint):
    """The CHECK constraints are the last line: they hold even if the API is bypassed."""
    match = await start_match(sessions, player)

    async with sessions() as session:
        session.add(
            Score(match_id=match.id, score=figures[0], pieces_dropped=figures[1],
                  highest_tier=figures[2])
        )
        with pytest.raises(IntegrityError, match=constraint):
            await session.flush()


async def test_the_database_allows_one_score_per_match(sessions, player):
    match = await start_match(sessions, player)
    await submit(sessions, player, match.id)

    async with sessions() as session:
        session.add(Score(match_id=match.id, score=1, pieces_dropped=1, highest_tier=1))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_the_database_has_no_leftover_locks(sessions, player):
    """Every test above ends with the lock released: nothing is left waiting in Postgres."""
    match = await start_match(sessions, player)
    await submit(sessions, player, match.id)

    async with sessions() as session:
        waiting = await session.scalar(
            text("SELECT count(*) FROM pg_locks WHERE NOT granted")
        )
        stored = await session.scalar(select(Score.match_id).where(Score.match_id == match.id))
    assert waiting == 0
    assert stored == match.id
