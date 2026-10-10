import random
from datetime import UTC, datetime
from uuid import UUID

from redis import RedisError

from coika_game_service.api.core.config import settings
from coika_game_service.api.repositories.protocols.cache_repository import CacheRepository

LEADERBOARD_KEY = "leaderboard:{}"
LEADERBOARD_KEY_DAILY = "leaderboard:{}:{}"
PLAYER_KEY = "player:name:{}"

# The names of a ranking page are cached at the same moment, so without jitter they would all expire
# together and every request at that instant would ask auth for the same ids (cache stampede).
TTL_JITTER = 0.2

class RedisRepository(CacheRepository):
    """
    Redis repository for caching data
    """
    def __init__(self, cache: RedisError):
        """
        Initialize the redis repository with a redis client instance
        """
        self.redis = cache

    async def update_max_score(self, player_id: str, game_mode: str, score: int):
        """
        Update the max score for a player in a game mode
        """
        try:
            # gt=True keeps the best score; the all-time ranking never expires
            await self.redis.zadd(
                LEADERBOARD_KEY.format(game_mode),
                {str(player_id): score},
                gt=True)
        except RedisError:
            return

    async def update_max_score_daily(
        self, 
        player_id: str,
        game_mode: str,
        score: int,
        seed: int):
        """
        Update the max score for a player in a game mode daily
        """
        key = LEADERBOARD_KEY_DAILY.format(game_mode, seed)
        try:
            # zadd has no TTL option: the expiry is a separate command, sent together with it
            async with self.redis.pipeline() as pipe:
                pipe.zadd(key, {str(player_id): score}, gt=True)
                pipe.expire(key, settings.SCORES_TTL_SECONDS)
                await pipe.execute()
        except RedisError:
            return

    async def get_players_profiles(self, player_ids: list[UUID]):
        """
        Get players profiles from cache
        """
        try:
            keys = [PLAYER_KEY.format(player_id) for player_id in player_ids]
            return await self.redis.mget(keys)
        except RedisError:
            return {}

    async def set_players_profiles(self, player_profiles: dict[UUID, str]):
        """ 
        Set players profiles in cache
        """
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for player_id, name in player_profiles.items():
                    pipe.set(PLAYER_KEY.format(player_id), name, ex=self._ttl_with_jitter())
                await pipe.execute()
        except RedisError:
            return

        
    def _ttl_with_jitter(self) -> int:
        """
        The configured TTL varied by +-TTL_JITTER, so cached names expire spread over time.
        """
        return max(1, round(settings.PLAYER_NAME_CACHE_TTL_SECONDS * 
                            random.uniform(1 - TTL_JITTER, 1 + TTL_JITTER)))

    async def get_leaderboard(self, game_mode: str, limit: int = 50):
        """
        Get the leaderboard for a game mode
        """
        try:
            return await self.redis.zrevrange(
                LEADERBOARD_KEY.format(game_mode),
                0,
                limit-1,
                withscores=True)
        except RedisError:
            return []
    
    async def get_daily_leaderboard(
            self, 
            game_mode: str,
            limit: int = 50,
            seed: int = lambda: datetime.now(UTC)):
        """
        Get the daily leaderboard
        """
        try:
            return await self.redis.zrevrange(
                LEADERBOARD_KEY_DAILY.format(game_mode, seed),
                0,
                limit-1,
                withscores=True)
        except RedisError:
            return []