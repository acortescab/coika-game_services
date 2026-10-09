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
        Creates a match, or returns the one already created with the same idempotency key.
        Returns the match and whether it was created now (False means it is a retry).
        Safe with concurrent requests that share the same key.
        """
        if not await self.repo.game_mode_exists(game_mode_id):
            raise GameModeNotFound(str(game_mode_id))

        match = await self.repo.create_match(player_id, game_mode_id, idempotency_key)
        if match is not None:
            await self.repo.commit()
            return match, True

        # The key was already used: the transaction was rolled back, so read the original
        match = await self.repo.get_by_idempotency_key(
            player_id, idempotency_key, use_writer=True
        )
        if match is None or match.game_mode_id != game_mode_id:
            raise IdempotencyKeyReused(str(idempotency_key))

        return match, False
