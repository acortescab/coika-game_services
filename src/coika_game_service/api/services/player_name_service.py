from collections.abc import Iterable
from uuid import UUID

import httpx

from coika_game_service.api.clients.auth_client import AuthClient
from coika_game_service.api.repositories.protocols.cache_repository import CacheRepository


def fallback_name(player_id: UUID) -> str:
    """
    Name shown when the real one cannot be resolved (auth down, deleted player).
    It is never cached, so the real name appears as soon as it can be resolved.
    """
    return f"player_{player_id.hex[:8]}"


class PlayerNameService:
    """
    Resolves the display name of players. The auth service is the only source of truth; this service
    keeps a short-lived Redis cache so a ranking page does not hit auth on every request.
    A failure of the auth service or of Redis never breaks the caller: it degrades to fallback
    names.
    """

    def __init__(self, auth_client: AuthClient, cache: CacheRepository):
        """
        Initializes the service with the auth client and a cache client.
        """
        self.auth_client = auth_client
        self.cache = cache

    async def get_names(self, player_ids: Iterable[UUID], token: str) -> dict[UUID, str]:
        """
        Returns {player_id: name} for every requested id. Never raises because of auth or Redis.
        `token` is the caller's access token, forwarded to the auth service for the cache misses.
        """
        unique_ids = list(dict.fromkeys(player_ids))
        names = await self._from_cache(unique_ids)

        missing = [player_id for player_id in unique_ids if player_id not in names]
        if missing:
            fetched = await self._from_auth(missing, token)
            await self._to_cache(fetched)
            names.update(fetched)

        return {
            player_id: names.get(player_id, fallback_name(player_id)) for player_id in unique_ids
        }

    async def _from_cache(self, player_ids: list[UUID]) -> dict[UUID, str]:
        """
        Reads the cached names; a Redis failure counts as a miss for everything.
        """
        if not player_ids:
            return {}

        values = await self.cache.get_players_profiles(player_ids)
        if values:
            return {
                player_id: value.decode() if isinstance(value, bytes) else value
                for player_id, value in zip(player_ids, values, strict=True)
                if value is not None
            }
        else:
            return {}
        

    async def _from_auth(self, player_ids: list[UUID], token: str) -> dict[UUID, str]:
        """
        Asks the auth service for the missing names; any failure counts as "nothing found".
        """
        try:
            return await self.auth_client.get_player_names(player_ids, token)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {}

    async def _to_cache(self, names: dict[UUID, str]) -> None:
        """
        Stores the names with a TTL; a Redis failure only means they will be asked again.
        """
        if not names:
            return

        await self.cache.set_players_profiles(names)
