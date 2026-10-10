from datetime import UTC, datetime
from uuid import UUID

from coika_game_service.api.core.exceptions import (
    GameModeNotFound,
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.game_modes import daily_seed
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.protocols.cache_repository import CacheRepository
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.api.services.player_name_service import PlayerNameService


class LeaderboardService:
    """
    This class should communicate with redis to get the leaderboard for a specific game mode
    """
    def __init__(
            self, 
            cache: CacheRepository, 
            game_mode_repo: GameModeRepository, 
            player_name_service: PlayerNameService):
        """
        Initialize the leaderboard service with a redis cache
        """
        self.cache = cache
        self.game_mode_repo = game_mode_repo
        self.player_name_service = player_name_service

    async def get_leaderboard(
            self, 
            token: str,
            game_mode_id: str, 
            limit: int = 50, 
            date: str | None = None):
        """
        Get the leaderboard for a specific game mode
        """
        game_mode = await self.game_mode_repo.get_game_mode(game_mode_id)
        if not game_mode:
            raise GameModeNotFound(str(game_mode_id))

        if game_mode.game_mode != GameModeName.DAILY:
            if date:
                raise NonDailyLeaderboardWithDate(
                    f"game_mode_id: {str(game_mode_id)}, date: {date}")

            results = await self.cache.get_leaderboard(game_mode_id, limit)
        else:
            date = date or str(daily_seed(datetime.now(UTC)))
            try:
                # strptime alone accepts "2026101" as 2026-10-01, which is another Redis key
                if len(date) != 8:
                    raise ValueError(date)
                datetime.strptime(date, "%Y%m%d")
            except ValueError as err:
                raise LeaderboardInvalidDate(date) from err
            
            results = await self.cache.get_daily_leaderboard(game_mode_id, limit, date)

        return await self._agreggate_names(results, token)

    async def _agreggate_names(self, results, token: str) -> list[LeaderboardResponse]:
        """
        Name aggreggations for laderboards
        """
        # Redis gives the ids as text (or bytes without decode_responses); the name service
        # works with UUIDs.
        ranking = [
            (UUID(player_id.decode() if isinstance(player_id, bytes) else player_id), score)
            for player_id, score in results
        ]
        names = await self.player_name_service.get_names([uuid for uuid, _ in ranking], token)

        response = []
        for uuid, score in ranking:
            response.append(
                LeaderboardResponse(
                    player_id=str(uuid),
                    name=names[uuid],
                    score=int(score),
                )
            )
        return response
        