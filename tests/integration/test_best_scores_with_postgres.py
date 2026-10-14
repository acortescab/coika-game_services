import uuid

import pytest

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID, DAILY_GAME_MODE_ID
from coika_game_service.api.db.models import (
    GameModeName,
    Match,
    MatchStatus,
    RejectReason,
    Score,
)
from coika_game_service.api.repositories.score_repository import ScoreRepository

pytestmark = pytest.mark.integration

CLASSIC = GameModeName.CLASSIC
DAILY = GameModeName.DAILY
ZEN = GameModeName.ZEN


@pytest.fixture
async def zen_id(other_mode):
    return other_mode


async def add_match(sessions, player_id, game_mode_id, score=None, status=MatchStatus.FINISHED,
                    seed=None) -> uuid.UUID:
    """
    Inserts a match straight into the database and, when `score` is given, its score.
    The `player` fixtures remove them afterwards.
    """
    reason = RejectReason.SCORE_MAX if status == MatchStatus.REJECTED else None
    async with sessions() as session:
        match = Match(
            player_id=player_id, game_mode_id=game_mode_id, idempotency_key=uuid.uuid4(),
            status=status, seed=seed, rejection_reason=reason)
        session.add(match)
        await session.flush()
        if score is not None:
            session.add(Score(match_id=match.id, score=score, pieces_dropped=10, highest_tier=1))
        await session.commit()
        return match.id


async def best_scores(sessions, players, **kwargs) -> dict:
    """
    Runs the real query and keeps only the rows of the test players: the database may hold
    other data. {(player_id, game_mode, seed): best_score}
    """
    async with sessions() as session:
        repo = ScoreRepository(session, session)
        result = {}
        async for batch in repo.get_best_scores_batch(**kwargs):
            for row in batch:
                if row.player_id in players:
                    result[(row.player_id, row.game_mode, row.seed)] = row.best_score
        return result


async def test_the_best_score_of_each_player_and_mode_wins(sessions, player, other_player):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=100)
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=900)
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=400)
    await add_match(sessions, other_player, CLASSIC_GAME_MODE_ID, score=300)

    result = await best_scores(sessions, {player, other_player})

    assert result == {
        (player, CLASSIC, None): 900,
        (other_player, CLASSIC, None): 300,
    }


async def test_only_finished_matches_count(sessions, player):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=500)
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=999999,
                    status=MatchStatus.REJECTED)
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=999999,
                    status=MatchStatus.IN_PROGRESS)
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, status=MatchStatus.ABANDONED)

    result = await best_scores(sessions, {player})

    assert result == {(player, CLASSIC, None): 500}


async def test_a_player_with_no_finished_match_is_absent(sessions, player):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=999999,
                    status=MatchStatus.REJECTED)

    assert await best_scores(sessions, {player}) == {}


async def test_each_game_mode_has_its_own_row(sessions, player, zen_id):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=700)
    await add_match(sessions, player, zen_id, score=200)

    result = await best_scores(sessions, {player})

    assert result == {(player, CLASSIC, None): 700, (player, ZEN, None): 200}


async def test_each_daily_seed_has_its_own_row(sessions, player):
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=100, seed=20261010)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=800, seed=20261010)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=300, seed=20261009)

    result = await best_scores(sessions, {player})

    assert result == {
        (player, DAILY, 20261010): 800,
        (player, DAILY, 20261009): 300,
    }


async def test_game_mode_filter_returns_only_that_mode(sessions, player, zen_id):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=700)
    await add_match(sessions, player, zen_id, score=200)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=500, seed=20261010)

    result = await best_scores(sessions, {player}, game_mode=CLASSIC)

    assert result == {(player, CLASSIC, None): 700}


async def test_cutoff_drops_old_dailies_but_keeps_modes_without_seed(sessions, player, zen_id):
    await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=700)
    await add_match(sessions, player, zen_id, score=200)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=500, seed=20261010)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=900, seed=20260101)

    result = await best_scores(sessions, {player}, daily_seed_cutoff=20261001)

    # classic and zen have a NULL seed: `NULL >= cutoff` must not make them disappear
    assert result == {
        (player, CLASSIC, None): 700,
        (player, ZEN, None): 200,
        (player, DAILY, 20261010): 500,
    }


async def test_batches_respect_the_batch_size(sessions, player, other_player):
    for _ in range(3):
        await add_match(sessions, player, CLASSIC_GAME_MODE_ID, score=10)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=10, seed=20261010)
    await add_match(sessions, player, DAILY_GAME_MODE_ID, score=10, seed=20261009)
    await add_match(sessions, other_player, CLASSIC_GAME_MODE_ID, score=10)

    async with sessions() as session:
        repo = ScoreRepository(session, session)
        sizes = [len(batch) async for batch in repo.get_best_scores_batch(batch_size=2)]

    assert sizes and all(size <= 2 for size in sizes)
    assert sum(sizes) >= 4  # at least the rows of this test (the database may hold more)
