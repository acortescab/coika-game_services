from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID


class CacheRepository(Protocol):
    """
    This is a prototype for a cache repository
    """

    async def update_max_score(self, player_id: str, game_mode: str, score: int):
        pass

    async def update_max_score_daily(
            self,
            player_id: str,
            game_mode: str,
            score: int, 
            seed: int):
        pass

    async def get_players_profiles(self, player_ids: list[UUID]):
        pass
    
    async def set_players_profiles(self, player_profiles: dict[UUID, str]):
        pass

    async def get_leaderboard(self, game_mode: str, limit: int = 50):
        pass

    async def get_daily_leaderboard(
            self,
            game_mode: str,
            limit: int = 50, 
            seed: int = lambda: datetime.now(UTC)):
        pass

    async def hit_rate_limit(self, player_id: UUID, prefix: str) -> tuple[bool, int, int]:
        """
        Counts a request; returns (blocked, remaining, seconds until the window resets).
        """
        pass