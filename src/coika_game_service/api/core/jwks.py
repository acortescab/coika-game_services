import asyncio
import logging
import time

import httpx
import jwt

logger = logging.getLogger(__name__)


class JWKSError(Exception):
    """Base class for key lookup problems."""
    

class UnknownKeyError(JWKSError):
    """The kid is not published by the auth service (even after refreshing)."""


class JWKSUnavailable(JWKSError):
    """The key set could not be downloaded and there is no cached copy."""


class JWKSCache:
    """
    Cache for JSON Web Key Sets.
    """
    def __init__(self, url: str, ttl_seconds: int, 
                 client: httpx.AsyncClient, min_refresh_interval: float = 30.0):
        """
        Initialize the cache.
        """
        self._url = url
        self._ttl = ttl_seconds
        self._client = client
        self._min_interval = min_refresh_interval

        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None 
        self._lock = asyncio.Lock()

    def is_fresh(self) -> bool:
        """
        Check if the cached keys are still fresh.
        """
        return (
            self._fetched_at is not None
            and time.monotonic() - self._fetched_at < self._ttl
        )

    async def get_key(self, kid: str) -> jwt.PyJWK:
        """
        Get a key by key ID.
        """
        key = self._keys.get(kid)
        if key is not None and self.is_fresh():
            return key

        await self.refresh()

        key = self._keys.get(kid)
        if key is None:
            raise UnknownKeyError(kid)

        return key

    async def refresh(self) -> None:
        """
        Refresh the key set.
        """
        seen = self._fetched_at
        async with self._lock:
            if self._fetched_at != seen:
                # Another task has already refreshed the keys
                return

            now = time.monotonic()
            if(
                self._keys 
                and self._last_attempt is not None
                and now - self._last_attempt < self._min_interval
            ):
                # Refresh has been made in interval phase
                return

            self._last_attempt = now

            try:
                response = await self._client.get(self._url)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
                keys = {}
                for key in key_set.keys:
                    if key.key_id:
                        keys[key.key_id] = key

            except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
                if not self._keys:
                    raise JWKSUnavailable(str(exc)) from exc
                logger.warning("JWKS refresh failed, serving cached keys: %s", exc)
                return

            self._keys = keys
            self._fetched_at = time.monotonic()