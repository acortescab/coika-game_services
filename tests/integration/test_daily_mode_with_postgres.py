import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from coika_game_service.api.core.game_modes import (
    CLASSIC_GAME_MODE_ID,
    DAILY_GAME_MODE_ID,
    GAME_MODES,
)
from coika_game_service.api.db.models import GameMode, GameModeName, Match, MatchStatus
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services.match_service import (
    GameModeNotFound,
    IdempotencyKeyReused,
    MatchService,
)
from coika_game_service.api.services.score_service import ScoreService
from tests.integration.conftest import RecordingCache, matches_of

pytestmark = pytest.mark.integration

OCT_10 = datetime(2026, 10, 10, 15, 0, 0, tzinfo=UTC)


async def start(
    sessions, player_id, now=OCT_10, mode_id=DAILY_GAME_MODE_ID, key=None
) -> tuple[Match, bool]:
    """One request at the given moment: its own session, a new idempotency key by default."""
    async with sessions() as session:
        service = MatchService(
            MatchRepository(session, session),
            GameModeRepository(session, session),
            session,
            clock=lambda: now,
        )
        return await service.create_match(player_id, mode_id, key or uuid.uuid4())


async def test_the_catalog_has_classic_and_daily_with_the_fixed_ids(sessions):
    async with sessions() as session:
        rows = (await session.execute(select(GameMode.id, GameMode.game_mode))).all()

    in_database = {name: mode_id for mode_id, name in rows}
    for name, mode_id in GAME_MODES.items():
        assert in_database[name] == mode_id


async def test_the_catalog_has_exactly_the_three_modes_of_the_enum(sessions):
    async with sessions() as session:
        names = set((await session.scalars(select(GameMode.game_mode))).all())

    assert names == {mode.value for mode in GameModeName}


async def test_an_id_that_is_not_in_the_catalog_is_refused(sessions, player):
    with pytest.raises(GameModeNotFound):
        await start(sessions, player, mode_id=uuid.uuid4())


async def test_a_daily_match_gets_the_seed_of_the_utc_date(sessions, player):
    match, created = await start(sessions, player, now=OCT_10)

    assert created is True
    assert match.seed == 20261010
    [stored] = await matches_of(sessions, player)
    assert stored.seed == 20261010


async def test_a_classic_match_has_no_seed(sessions, player):
    match, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)

    assert match.seed is None
    [stored] = await matches_of(sessions, player)
    assert stored.seed is None


async def test_everybody_gets_the_same_seed_on_the_same_day(sessions, player, other_player):
    morning = datetime(2026, 10, 10, 0, 0, 1, tzinfo=UTC)
    night = datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)

    first, _ = await start(sessions, player, now=morning)
    second, _ = await start(sessions, other_player, now=night)

    assert first.seed == second.seed == 20261010


async def test_the_seed_changes_with_the_day(sessions, player):
    today, _ = await start(sessions, player, now=OCT_10)
    tomorrow, _ = await start(sessions, player, now=OCT_10 + timedelta(days=1))

    assert (today.seed, tomorrow.seed) == (20261010, 20261011)


async def test_a_retry_gets_the_original_seed_even_if_the_day_changed(sessions, player):
    key = uuid.uuid4()
    first, _ = await start(sessions, player, now=OCT_10, key=key)

    retry, created = await start(sessions, player, now=OCT_10 + timedelta(days=1), key=key)

    assert created is False
    assert retry.id == first.id
    assert retry.seed == 20261010
    assert len(await matches_of(sessions, player)) == 1


async def test_a_match_started_just_before_midnight_belongs_to_that_day(sessions, player):
    last_moment = datetime(2026, 10, 10, 23, 59, 59, 999_999, tzinfo=UTC)

    match, _ = await start(sessions, player, now=last_moment)

    [stored] = await matches_of(sessions, player)
    assert stored.started_at == last_moment
    assert stored.seed == 20261010
    # the seed's date and the date of started_at are the same by construction
    assert stored.started_at.astimezone(UTC).date().isoformat() == "2026-10-10"
    assert match.seed == 20261010


async def test_a_match_started_at_midnight_belongs_to_the_new_day(sessions, player):
    midnight = datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)

    await start(sessions, player, now=midnight)

    [stored] = await matches_of(sessions, player)
    assert stored.started_at == midnight
    assert stored.seed == 20261011


async def test_finishing_a_match_does_not_change_its_seed_or_its_day(sessions, player):
    """The match keeps the seed and the date of its start, whenever its score arrives."""
    started = datetime(2026, 10, 10, 23, 59, 0, tzinfo=UTC)
    match, _ = await start(sessions, player, now=started)

    await submit(sessions, player, match.id, score=100)

    [stored] = await matches_of(sessions, player)
    assert stored.status == MatchStatus.FINISHED
    assert stored.seed == 20261010
    assert stored.started_at == started


async def test_a_daily_score_is_ranked_on_the_date_the_match_started_not_the_one_it_was_sent(
    sessions, player
):
    """Started on Oct 10, sent today: the ranking that gets it is the one of Oct 10."""
    match, _ = await start(sessions, player, now=OCT_10 - timedelta(days=3))
    cache = RecordingCache()

    await submit(sessions, player, match.id, score=100, cache=cache)

    assert cache.calls == [("daily", player, GameModeName.DAILY, 100, 20261007)]


async def test_a_classic_score_is_ranked_by_the_name_of_the_mode(sessions, player):
    match, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)
    cache = RecordingCache()

    await submit(sessions, player, match.id, score=100, cache=cache)

    assert cache.calls == [("classic", player, GameModeName.CLASSIC, 100)]


async def test_a_second_worse_score_in_the_same_mode_is_not_ranked_again(sessions, player):
    first, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)
    await submit(sessions, player, first.id, score=500)
    second, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)
    cache = RecordingCache()

    await submit(sessions, player, second.id, score=400, cache=cache)

    assert cache.calls == []


async def submit(sessions, player_id, match_id, score, cache=None):
    """Submits a score like a request would: its own session."""
    async with sessions() as session:
        service = ScoreService(
            ScoreRepository(session, session),
            MatchRepository(session, session),
            cache or RecordingCache(),
            GameModeRepository(session, session),
            session,
        )
        return await service.create_score(
            player_id, match_id,
            CreateScoreRequest(score=score, pieces_dropped=20, highest_tier=3),
        )


async def test_daily_retries_are_unlimited_and_each_one_abandons_the_previous(sessions, player):
    for attempt in range(8):
        await start(sessions, player, now=OCT_10 + timedelta(minutes=attempt))

    stored = await matches_of(sessions, player)
    assert len(stored) == 8
    assert sum(m.status == MatchStatus.IN_PROGRESS for m in stored) == 1
    assert sum(m.status == MatchStatus.ABANDONED for m in stored) == 7
    assert {m.seed for m in stored} == {20261010}


async def test_starting_daily_abandons_an_open_classic_match_and_the_other_way_round(
    sessions, player
):
    classic, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)
    daily, _ = await start(sessions, player, mode_id=DAILY_GAME_MODE_ID)
    classic_again, _ = await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID)

    by_id = {m.id: m for m in await matches_of(sessions, player)}
    assert by_id[classic.id].status == MatchStatus.ABANDONED
    assert by_id[daily.id].status == MatchStatus.ABANDONED
    assert by_id[classic_again.id].status == MatchStatus.IN_PROGRESS


async def test_the_same_key_for_daily_and_classic_is_refused(sessions, player):
    key = uuid.uuid4()
    await start(sessions, player, mode_id=DAILY_GAME_MODE_ID, key=key)

    with pytest.raises(IdempotencyKeyReused):
        await start(sessions, player, mode_id=CLASSIC_GAME_MODE_ID, key=key)


async def test_concurrent_daily_starts_all_succeed_with_the_same_seed_and_one_open(
    sessions, player
):
    results = await asyncio.gather(*(start(sessions, player) for _ in range(8)))

    assert all(created for _, created in results)
    assert {match.seed for match, _ in results} == {20261010}
    stored = await matches_of(sessions, player)
    assert len(stored) == 8
    assert sum(m.status == MatchStatus.IN_PROGRESS for m in stored) == 1


async def test_concurrent_retries_of_a_daily_start_create_one_match(sessions, player):
    key = uuid.uuid4()

    results = await asyncio.gather(*(start(sessions, player, key=key) for _ in range(8)))

    assert len({match.id for match, _ in results}) == 1
    assert {match.seed for match, _ in results} == {20261010}
    assert len(await matches_of(sessions, player)) == 1


async def test_the_default_clock_is_the_real_utc_time(sessions, player):
    before = datetime.now(UTC)
    async with sessions() as session:
        service = MatchService(
            MatchRepository(session, session), GameModeRepository(session, session), session
        )
        match, _ = await service.create_match(player, DAILY_GAME_MODE_ID, uuid.uuid4())
    after = datetime.now(UTC)

    assert before <= match.started_at <= after
    assert match.seed in {
        int(before.strftime("%Y%m%d")),
        int(after.strftime("%Y%m%d")),
    }
