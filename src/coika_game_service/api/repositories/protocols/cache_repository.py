from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID


class CacheRepository(Protocol):
    """
    This is a prototype for a cache repository
    """

    async def update_max_score(self, player_id: str, game_mode_id: str, score: int):
        pass

    async def update_max_score_daily(
            self, 
            player_id: str, 
            game_mode_id: str, 
            score: int, 
            seed: int):
        pass

    async def get_players_profiles(self, player_ids: list[UUID]):
        pass
    
    async def set_players_profiles(self, player_profiles: dict[UUID, str]):
        pass

    async def get_leaderboard(self, game_mode_id: str, limit: int = 50):
        pass

    async def get_daily_leaderboard(
            self,  
            game_mode_id: str, 
            limit: int = 50, 
            seed: int = lambda: datetime.now(UTC)):
        pass