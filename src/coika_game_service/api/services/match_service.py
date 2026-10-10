from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.core.exceptions import GameModeNotFound, IdempotencyKeyReused
from coika_game_service.api.core.game_modes import daily_seed, is_daily
from coika_game_service.api.db.models import Match
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository


class MatchService:
    """
    Match creation and management service
    """
    def __init__(
        self,
        match_repo: MatchRepository,
        game_mode_repo: GameModeRepository,
        write_db: AsyncSession,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        """
        Initializes the MatchService. `write_db` must be the session the repository writes
        with: the repositories only flush, and this service commits the transaction.
        `clock` returns the current time with a time zone; tests replace it to control the day.
        """
        self.match_repo = match_repo
        self.game_mode_repo = game_mode_repo
        self.write_db = write_db
        self.clock = clock

    async def create_match(
        self, player_id: UUID, game_mode_id: UUID, idempotency_key: UUID
    ) -> tuple[Match, bool]:
        """
        Starts a match, or returns the one already created with the same idempotency key.
        A player plays one match at a time: the unfinished one is abandoned when a new one
        starts. Returns the match and whether it was created now (False means it is a retry).
        Requests of the same player run one after another (see lock_player), so concurrent
        requests, with the same key or with different keys, cannot collide.
        """
        if not await self.game_mode_repo.get_game_mode(game_mode_id):
            raise GameModeNotFound(str(game_mode_id))

        # Held until the commit: whoever waits here sees the result of the previous request
        await self.match_repo.lock_player(player_id)

        # A retry is answered before closing anything, even if its match was closed since
        existing = await self.match_repo.get_by_idempotency_key(
            player_id, idempotency_key, use_writer=True
        )
        if existing is not None:
            if existing.game_mode_id != game_mode_id:
                # The key was already used for a different game mode, trying to reuse the key
                raise IdempotencyKeyReused(str(idempotency_key))
            # idempotency key exists, It is a retry
            return existing, False

        # One moment for both: the seed's date is the date the match starts on, even at midnight
        now = self.clock()
        seed = daily_seed(now) if is_daily(game_mode_id) else None

        await self.match_repo.abandon_open_matches(player_id)
        match = await self.match_repo.create_match(
            player_id, game_mode_id, idempotency_key, started_at=now, seed=seed
        )
        await self.write_db.commit()
        return match, True
