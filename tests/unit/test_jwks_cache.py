import asyncio

import httpx
import pytest

from coika_game_service.api.core import jwks as jwks_module
from coika_game_service.api.core.jwks import JWKSCache, JWKSUnavailable, UnknownKeyError
from tests.auth_helpers import KID, make_jwks, make_keypair


class FakeAuthServer:
    """JWKS endpoint that counts requests and can be rotated or taken down."""

    def __init__(self, *keypairs, delay: float = 0.0):
        self.keys = {KID: keypairs[0]} if keypairs else {}
        self.delay = delay
        self.down = False
        self.calls = 0

    def publish(self, kid, keypair):
        self.keys[kid] = keypair

    def jwks(self):
        keys = []
        for kid, keypair in self.keys.items():
            keys.extend(make_jwks(keypair, kid=kid)["keys"])
        return {"keys": keys}

    async def handler(self, _request):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            return httpx.Response(503)
        return httpx.Response(200, json=self.jwks())

    def cache(self, ttl=300, min_refresh_interval=0.0) -> JWKSCache:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return JWKSCache("http://auth/jwks", ttl, client, min_refresh_interval)


@pytest.fixture
def server():
    return FakeAuthServer(make_keypair())


async def test_known_key_is_served_from_cache(server):
    cache = server.cache()

    await cache.get_key(KID)
    await cache.get_key(KID)

    assert server.calls == 1


async def test_unknown_kid_refreshes_once_and_picks_up_the_rotated_key(server):
    cache = server.cache()
    await cache.get_key(KID)

    server.publish("rotated", make_keypair())
    key = await cache.get_key("rotated")

    assert key.key_id == "rotated"
    assert server.calls == 2  # one initial fetch + exactly one refresh


async def test_unknown_kid_that_stays_unknown_is_rejected_after_one_refresh(server):
    cache = server.cache()
    await cache.get_key(KID)

    with pytest.raises(UnknownKeyError):
        await cache.get_key("nope")

    assert server.calls == 2


async def test_unknown_kid_flood_does_not_hammer_the_auth_service(server):
    """With a refresh interval, junk kids cannot force one fetch per request."""
    cache = server.cache(min_refresh_interval=30.0)
    await cache.get_key(KID)

    for _ in range(5):
        with pytest.raises(UnknownKeyError):
            await cache.get_key("nope")

    assert server.calls == 1


async def test_concurrent_requests_trigger_a_single_fetch():
    server = FakeAuthServer(make_keypair(), delay=0.05)
    cache = server.cache()

    keys = await asyncio.gather(*(cache.get_key(KID) for _ in range(20)))

    assert server.calls == 1
    assert all(key.key_id == KID for key in keys)


async def test_concurrent_unknown_kid_triggers_a_single_refresh():
    server = FakeAuthServer(make_keypair(), delay=0.05)
    cache = server.cache()
    await cache.get_key(KID)
    server.publish("rotated", make_keypair())

    await asyncio.gather(*(cache.get_key("rotated") for _ in range(20)))

    assert server.calls == 2


async def test_expired_ttl_triggers_a_refresh(server, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(jwks_module.time, "monotonic", lambda: now[0])
    cache = server.cache(ttl=300)
    await cache.get_key(KID)

    now[0] += 299
    await cache.get_key(KID)
    assert server.calls == 1

    now[0] += 2
    await cache.get_key(KID)
    assert server.calls == 2


async def test_stale_cache_is_served_when_the_auth_service_goes_down(server, monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(jwks_module.time, "monotonic", lambda: now[0])
    cache = server.cache(ttl=300)
    await cache.get_key(KID)

    server.down = True
    now[0] += 1000
    with caplog.at_level("WARNING", logger=jwks_module.logger.name):
        key = await cache.get_key(KID)

    assert key.key_id == KID
    assert server.calls == 2
    assert "JWKS refresh failed" in caplog.text


async def test_cached_keys_survive_a_failed_refresh_for_unknown_kid(server):
    cache = server.cache()
    await cache.get_key(KID)

    server.down = True
    with pytest.raises(UnknownKeyError):
        await cache.get_key("nope")

    assert (await cache.get_key(KID)).key_id == KID


async def test_without_cache_a_down_auth_service_is_unavailable(server):
    server.down = True
    cache = server.cache()

    with pytest.raises(JWKSUnavailable):
        await cache.get_key(KID)


async def test_invalid_jwks_body_without_cache_is_unavailable():
    def handler(_request):
        return httpx.Response(200, json={"not": "a jwks"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache("http://auth/jwks", 300, client)

    with pytest.raises(JWKSUnavailable):
        await cache.get_key(KID)


async def test_cache_recovers_after_the_auth_service_comes_back(server):
    server.down = True
    cache = server.cache()
    with pytest.raises(JWKSUnavailable):
        await cache.get_key(KID)

    server.down = False
    assert (await cache.get_key(KID)).key_id == KID
