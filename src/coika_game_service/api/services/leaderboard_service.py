from datetime import UTC, datetime
from uuid import UUID

from coika_game_service.api.core.exceptions import (
    LeaderboardInvalidDate,
    NonDailyLeaderboardWithDate,
)
from coika_game_service.api.core.game_modes import daily_seed
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.repositories.protocols.cache_repository import CacheRepository
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.api.services.player_name_service import PlayerNameService


class LeaderboardService:
    """
    This class should communicate with redis to get the leaderboard for a specific game mode.
    It never touches PostgreSQL: the game mode comes by name, validated by the route.
    """
    def __init__(self, cache: CacheRepository, player_name_service: PlayerNameService):
        """
        Initialize the leaderboard service with a redis cache and the player name service
        """
        self.cache = cache
        self.player_name_service = player_name_service

    async def get_leaderboard(
            self,
            token: str,
            game_mode: GameModeName,
            limit: int = 50,
            date: str | None = None):
        """
        Get the leaderboard for a specific game mode
        """
        if game_mode != GameModeName.DAILY:
            if date:
                raise NonDailyLeaderboardWithDate(f"game_mode: {game_mode}, date: {date}")

            results = await self.cache.get_leaderboard(game_mode, limit)
        else:
            date = date or str(daily_seed(datetime.now(UTC)))
            try:
                # strptime alone accepts "2026101" as 2026-10-01, which is another Redis key
                if len(date) != 8:
                    raise ValueError(date)
                datetime.strptime(date, "%Y%m%d")
            except ValueError as err:
                raise LeaderboardInvalidDate(date) from err
            
            results = await self.cache.get_daily_leaderboard(game_mode, limit, date)

        return await self._agreggate_names(results, token)

    async def get_leaderboard_me(
            self,
            token: str,
            player_id: UUID,
            game_mode: GameModeName,
            limit: int = 50,
            date: str | None = None):
        """
        The ranking around the player: up to `limit` entries above and below, with the real
        position of each one. Raises InvalidLeaderboardRank if the player has no score there.
        """
        if game_mode != GameModeName.DAILY:
            if date:
                raise NonDailyLeaderboardWithDate(f"game_mode: {game_mode}, date: {date}")

            first_position, results = await self.cache.get_leaderboard_me(
                player_id, game_mode, limit)
        else:
            date = date or str(daily_seed(datetime.now(UTC)))
            try:
                # strptime alone accepts "2026101" as 2026-10-01, which is another Redis key
                if len(date) != 8:
                    raise ValueError(date)
                datetime.strptime(date, "%Y%m%d")
            except ValueError as err:
                raise LeaderboardInvalidDate(date) from err

            first_position, results = await self.cache.get_daily_leaderboard_me(
                player_id, game_mode, limit, date)

        return await self._agreggate_names(results, token, first_position)

    async def _agreggate_names(
            self, results, token: str, first_position: int = 1) -> list[LeaderboardResponse]:
        """
        Name aggreggations for laderboards. `first_position` is the position of the first entry
        (1 for a ranking read from the top, the real one for the window around a player).
        """
        # Redis gives the ids as text (or bytes without decode_responses); the name service
        # works with UUIDs.
        ranking = [
            (UUID(player_id.decode() if isinstance(player_id, bytes) else player_id), score)
            for player_id, score in results
        ]
        names = await self.player_name_service.get_names([uuid for uuid, _ in ranking], token)

        response = []
        for position, (uuid, score) in enumerate(ranking, start=first_position):
            response.append(
                LeaderboardResponse(
                    player_id=str(uuid),
                    position=position,
                    name=names[uuid],
                    score=int(score),
                )
            )
        return response
        