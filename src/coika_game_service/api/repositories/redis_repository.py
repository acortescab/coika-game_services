import random
from datetime import UTC, datetime
from uuid import UUID

from redis import RedisError

from coika_game_service.api.core.config import settings
from coika_game_service.api.core.exceptions import CacheDown, InvalidLeaderboardRank
from coika_game_service.api.core.game_modes import daily_seed
from coika_game_service.api.repositories.protocols.cache_repository import CacheRepository

LEADERBOARD_KEY = "leaderboard:{}"
LEADERBOARD_KEY_DAILY = "leaderboard:{}:{}"
PLAYER_KEY = "player:name:{}"
RATE_LIMITER_KEY = "ratelimit:{}:{}"

# KEYS[1] = ranking, ARGV[1] = player, ARGV[2] = entries above and below the player.
# Returns nil if the player is not in the ranking, else {index of the first entry, flat
# [member, score, ...] list}. Rank and range are read in one call, so the ranking cannot move
# between the two.
AROUND_PLAYER_LUA = """
local rank = redis.call('ZREVRANK', KEYS[1], ARGV[1])
if not rank then
    return nil
end
local limit = tonumber(ARGV[2])
local first = math.max(0, rank - limit)
return {first, redis.call('ZREVRANGE', KEYS[1], first, rank + limit, 'WITHSCORES')}
"""

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
        self._around_script = None

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
        except RedisError as exc:
            raise CacheDown() from exc

    async def get_leaderboard_me(self, player_id: str, game_mode: str, limit: int = 25):
        """
        Get the all-time ranking around the player: up to `limit` entries above and below.
        Returns the position of the first entry and the (player_id, score) pairs.
        Raises InvalidLeaderboardRank if the player is not in the ranking.
        """
        return await self._around_player(
            LEADERBOARD_KEY.format(game_mode), player_id, game_mode, limit)

    async def get_daily_leaderboard(
            self,
            game_mode: str,
            limit: int = 50,
            seed: int | str | None = None):
        """
        Get the daily leaderboard (today's if no seed is given)
        """
        seed = seed or daily_seed(datetime.now(UTC))
        try:
            return await self.redis.zrevrange(
                LEADERBOARD_KEY_DAILY.format(game_mode, seed),
                0,
                limit-1,
                withscores=True)
        except RedisError as exc:
            raise CacheDown() from exc

    async def get_daily_leaderboard_me(
            self,
            player_id: str,
            game_mode: str,
            limit: int = 25,
            seed: int | str | None = None):
        """
        Get the daily ranking around the player: up to `limit` entries above and below.
        Returns the position of the first entry and the (player_id, score) pairs.
        Raises InvalidLeaderboardRank if the player is not in the ranking.
        """
        seed = seed or daily_seed(datetime.now(UTC))
        return await self._around_player(
            LEADERBOARD_KEY_DAILY.format(game_mode, seed), player_id, game_mode, limit)

    async def _around_player(self, key: str, player_id: str, game_mode: str, limit: int):
        """
        Reads the rank of the player and the window around it in one atomic Lua call.
        Returns (position of the first entry, [(player_id, score), ...]).
        """
        if self._around_script is None:
            self._around_script = self.redis.register_script(AROUND_PLAYER_LUA)
        try:
            result = await self._around_script(keys=[key], args=[str(player_id), limit])
        except RedisError as exc:
            raise CacheDown() from exc

        if result is None:
            raise InvalidLeaderboardRank(f"player_id:{player_id}, game_mode:{game_mode}")

        first_index, flat = result
        # Lua gives [member, score, member, score, ...] with the scores as text
        members, scores = flat[::2], flat[1::2]
        entries = [(m, float(s)) for m, s in zip(members, scores, strict=True)]
        return first_index + 1, entries

    async def hit_rate_limit(self, player_id: UUID, prefix: str) -> tuple[bool, int, int]:
        """
        Counts one request of the player for the operation `prefix` in the current window.
        INCR, EXPIRE NX and TTL run in one transaction, so the key always gets its expiry and
        later hits do not extend the window. Returns whether the player is over the limit,
        how many requests are left and the seconds until the window resets. If Redis is down
        the request is let through (the limit is lost, the service is not).
        """
        try:
            key = RATE_LIMITER_KEY.format(player_id, prefix)
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, settings.RATE_LIMIT_WINDOW, nx=True)
                pipe.ttl(key)
                count, _ , ttl = await pipe.execute()

            blocked = count > settings.RATE_LIMIT_COUNTER
            remaining = max(0, settings.RATE_LIMIT_COUNTER - count)
            return blocked, remaining, ttl
        except RedisError:
            return False, settings.RATE_LIMIT_COUNTER, 0
