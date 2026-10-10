import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.db.models import Match, MatchStatus
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.services.match_service import (
    GameModeNotFound,
    IdempotencyKeyReused,
    MatchService,
)
from tests.integration.conftest import RecordingCache, matches_of

pytestmark = pytest.mark.integration


async def start(sessions, player_id, key, mode_id=CLASSIC_GAME_MODE_ID):
    """One request: its own sessions, like the real dependencies."""
    async with sessions() as session:
        service = MatchService(
            MatchRepository(session, session),
            GameModeRepository(session, session),
            RecordingCache(),
            session,
        )
        return await service.create_match(player_id, mode_id, key)


async def test_first_match_is_created_in_progress(sessions, player):
    match, created = await start(sessions, player, uuid.uuid4())

    assert created is True
    assert match.status == MatchStatus.IN_PROGRESS
    assert match.player_id == player
    assert match.game_mode_id == CLASSIC_GAME_MODE_ID
    assert match.started_at is not None


async def test_retry_with_the_same_key_returns_the_original(sessions, player):
    key = uuid.uuid4()
    first, _ = await start(sessions, player, key)

    again, created = await start(sessions, player, key)

    assert created is False
    assert again.id == first.id
    assert len(await matches_of(sessions, player)) == 1


async def test_same_key_with_another_game_mode_is_rejected(sessions, player, other_mode):
    key = uuid.uuid4()
    await start(sessions, player, key)

    with pytest.raises(IdempotencyKeyReused):
        await start(sessions, player, key, mode_id=other_mode)


async def test_the_same_key_can_be_used_by_two_players(sessions, player):
    key = uuid.uuid4()
    other_player = uuid.uuid4()
    try:
        first, _ = await start(sessions, player, key)
        second, created = await start(sessions, other_player, key)
    finally:
        async with sessions() as session:
            await session.execute(delete(Match).where(Match.player_id == other_player))
            await session.commit()

    assert created is True
    assert first.id != second.id


async def test_starting_a_new_match_abandons_the_open_one(sessions, player):
    old, _ = await start(sessions, player, uuid.uuid4())

    new, created = await start(sessions, player, uuid.uuid4())

    assert created is True
    by_id = {m.id: m for m in await matches_of(sessions, player)}
    assert by_id[old.id].status == MatchStatus.ABANDONED
    assert by_id[old.id].finish_at is not None
    assert by_id[new.id].status == MatchStatus.IN_PROGRESS


async def test_the_open_match_is_abandoned_even_in_another_game_mode(
    sessions, player, other_mode
):
    old, _ = await start(sessions, player, uuid.uuid4(), mode_id=other_mode)

    await start(sessions, player, uuid.uuid4(), mode_id=CLASSIC_GAME_MODE_ID)

    by_id = {m.id: m for m in await matches_of(sessions, player)}
    assert by_id[old.id].status == MatchStatus.ABANDONED


async def test_retrying_an_abandoned_match_returns_it_and_keeps_the_new_one_open(
    sessions, player
):
    old_key = uuid.uuid4()
    old, _ = await start(sessions, player, old_key)
    new, _ = await start(sessions, player, uuid.uuid4())

    again, created = await start(sessions, player, old_key)

    assert created is False
    assert again.id == old.id
    by_id = {m.id: m for m in await matches_of(sessions, player)}
    assert by_id[new.id].status == MatchStatus.IN_PROGRESS


async def test_concurrent_requests_with_the_same_key_create_one_match(sessions, player):
    key = uuid.uuid4()

    results = await asyncio.gather(*(start(sessions, player, key) for _ in range(10)))

    assert sum(1 for _, created in results if created) == 1
    assert len({match.id for match, _ in results}) == 1
    assert len(await matches_of(sessions, player)) == 1


async def test_concurrent_requests_with_different_keys_all_succeed_and_leave_one_open(
    sessions, player
):
    """The player lock serializes them: nobody fails, only the last one stays open."""
    count = 10

    results = await asyncio.gather(*(start(sessions, player, uuid.uuid4()) for _ in range(count)))

    assert all(created for _, created in results)
    stored = await matches_of(sessions, player)
    assert len(stored) == count
    open_matches = [m for m in stored if m.status == MatchStatus.IN_PROGRESS]
    assert len(open_matches) == 1
    assert all(m.status == MatchStatus.ABANDONED for m in stored if m not in open_matches)


async def test_requests_of_different_players_do_not_wait_for_each_other(sessions, player):
    """A held lock only blocks the same player."""
    other_player = uuid.uuid4()
    try:
        async with sessions() as holder:
            await MatchRepository(holder, holder).lock_player(player)  # never committed here

            # Another player is not blocked by it (a timeout here would mean it was)
            _, created = await asyncio.wait_for(
                start(sessions, other_player, uuid.uuid4()), timeout=5
            )
            assert created is True

            # The same player does have to wait
            blocked = asyncio.ensure_future(start(sessions, player, uuid.uuid4()))
            await asyncio.sleep(0.3)
            assert not blocked.done()

            await holder.rollback()  # releases the lock
            _, created = await asyncio.wait_for(blocked, timeout=5)
            assert created is True
    finally:
        async with sessions() as session:
            await session.execute(delete(Match).where(Match.player_id == other_player))
            await session.commit()


async def test_database_refuses_two_open_matches_for_a_player(sessions, player):
    """The invariant lives in the database, not only in the service."""
    from sqlalchemy.exc import IntegrityError

    async with sessions() as session:
        for _ in range(2):
            session.add(
                Match(player_id=player, game_mode_id=CLASSIC_GAME_MODE_ID,
                      idempotency_key=uuid.uuid4())
            )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_unknown_game_mode_raises_and_creates_nothing(sessions, player):
    with pytest.raises(GameModeNotFound):
        await start(sessions, player, uuid.uuid4(), mode_id=uuid.uuid4())

    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(Match).where(Match.player_id == player)
        )
    assert count == 0
