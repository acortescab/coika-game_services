from uuid import UUID

from coika_game_service.api.db.models import Match
from coika_game_service.api.repositories.match_repository import MatchRepository


class IdempotencyKeyReused(Exception):
    """The idempotency key was already used by the player for a different game mode."""


class GameModeNotFound(Exception):
    """The game mode is not in the catalog."""


class MatchService:
    """
    Match creation and management service
    """
    def __init__(self, repo: MatchRepository):
        """
        Initializes the MatchService.
        """
        self.repo = repo

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
        if not await self.repo.game_mode_exists(game_mode_id):
            raise GameModeNotFound(str(game_mode_id))

        # Held until the commit: whoever waits here sees the result of the previous request
        await self.repo.lock_player(player_id)

        # A retry is answered before closing anything, even if its match was closed since
        existing = await self.repo.get_by_idempotency_key(
            player_id, idempotency_key, use_writer=True
        )
        if existing is not None:
            if existing.game_mode_id != game_mode_id:
                # The key was already used for a different game mode, trying to reuse the key
                raise IdempotencyKeyReused(str(idempotency_key))
            # idempotency key exists, it is a retry
            return existing, False

        await self.repo.abandon_open_matches(player_id)
        match = await self.repo.create_match(player_id, game_mode_id, idempotency_key)
        await self.repo.commit()
        return match, True
