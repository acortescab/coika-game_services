import json
import uuid

import httpx
import pytest
from redis.exceptions import RedisError

from coika_game_service.api.clients.auth_client import MAX_IDS_PER_REQUEST, AuthClient
from coika_game_service.api.services.player_name_service import (
    CACHE_KEY,
    PlayerNameService,
    fallback_name,
)

TOKEN = "caller-access-token"
TTL = 300


class FakePipeline:
    def __init__(self, store):
        self.store = store
        self.queued = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def set(self, key, value, ex=None):
        self.queued.append((key, value, ex))

    async def execute(self):
        for key, value, ex in self.queued:
            self.store.set(key, value, ex=ex)


class FakeCache:
    """Minimal async Redis: mget, set with TTL and pipeline. `fail` simulates Redis being down."""

    def __init__(self, fail=False):
        self.data = {}
        self.ttls = {}
        self.fail = fail

    def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex

    async def mget(self, keys):
        if self.fail:
            raise RedisError("redis down")
        return [self.data.get(key) for key in keys]

    def pipeline(self, transaction=True):
        if self.fail:
            raise RedisError("redis down")
        return FakePipeline(self)


def ids_of(request: httpx.Request) -> list[uuid.UUID]:
    """The ids the client asked for: they travel in the JSON body."""
    return [uuid.UUID(value) for value in json.loads(request.content)["ids"]]


class FakeAuth:
    """Stands in for the auth service behind httpx.MockTransport and records the requests."""

    def __init__(self, names=None, status=200, error=None):
        self.names = names or {}
        self.status = status
        self.error = error
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.status != 200:
            return httpx.Response(self.status)
        asked = ids_of(request)
        return httpx.Response(
            200, json=[{"id": str(i), "name": self.names[i]} for i in asked if i in self.names]
        )

    def asked_ids(self):
        return [ids_of(request) for request in self.requests]


def build(auth: FakeAuth, cache: FakeCache | None = None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(auth.handler))
    cache = cache if cache is not None else FakeCache()
    service = PlayerNameService(AuthClient(client, "http://auth/v0/players"), cache, TTL)
    return service, cache


async def test_names_come_from_auth_and_are_cached_with_a_ttl():
    ana, luis = uuid.uuid4(), uuid.uuid4()
    auth = FakeAuth({ana: "Ana", luis: "Luis"})
    service, cache = build(auth)

    names = await service.get_names([ana, luis], TOKEN)

    assert names == {ana: "Ana", luis: "Luis"}
    assert cache.data[CACHE_KEY.format(ana)] == b"Ana"
    assert TTL * 0.8 <= cache.ttls[CACHE_KEY.format(ana)] <= TTL * 1.2


async def test_ttls_are_spread_so_a_ranking_page_does_not_expire_at_once():
    """Names cached together must not all expire together (cache stampede on auth)."""
    ids = [uuid.uuid4() for _ in range(50)]
    service, cache = build(FakeAuth({player_id: "x" for player_id in ids}))

    await service.get_names(ids, TOKEN)

    ttls = list(cache.ttls.values())
    assert len(ttls) == 50
    assert all(TTL * 0.8 <= ttl <= TTL * 1.2 for ttl in ttls)
    assert len(set(ttls)) > 1, "all the names got exactly the same TTL"


async def test_ids_are_sent_in_the_body_with_post_and_not_in_the_url():
    """100 UUIDs in a URL would be ~4 KB: the lookup is a POST with a JSON body."""
    ana = uuid.uuid4()
    auth = FakeAuth({ana: "Ana"})
    service, _ = build(auth)

    await service.get_names([ana], TOKEN)

    request = auth.requests[0]
    assert request.method == "POST"
    assert request.url.query == b""
    assert json.loads(request.content) == {"ids": [str(ana)]}


async def test_cached_names_do_not_hit_auth():
    ana = uuid.uuid4()
    auth = FakeAuth({ana: "Ana"})
    service, _ = build(auth)
    await service.get_names([ana], TOKEN)
    auth.requests.clear()

    names = await service.get_names([ana], TOKEN)

    assert names == {ana: "Ana"}
    assert auth.requests == []


async def test_only_the_missing_names_are_requested():
    cached, missing = uuid.uuid4(), uuid.uuid4()
    auth = FakeAuth({missing: "Luis"})
    service, cache = build(auth)
    cache.set(CACHE_KEY.format(cached), "Ana")

    names = await service.get_names([cached, missing], TOKEN)

    assert names == {cached: "Ana", missing: "Luis"}
    assert auth.asked_ids() == [[missing]]


async def test_the_callers_token_is_forwarded_to_auth():
    ana = uuid.uuid4()
    auth = FakeAuth({ana: "Ana"})
    service, _ = build(auth)

    await service.get_names([ana], TOKEN)

    assert auth.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_duplicated_ids_are_resolved_once():
    ana = uuid.uuid4()
    auth = FakeAuth({ana: "Ana"})
    service, _ = build(auth)

    names = await service.get_names([ana, ana, ana], TOKEN)

    assert names == {ana: "Ana"}
    assert auth.asked_ids() == [[ana]]


async def test_a_player_unknown_to_auth_gets_the_fallback_name():
    ghost = uuid.uuid4()
    service, cache = build(FakeAuth({}))

    names = await service.get_names([ghost], TOKEN)

    assert names == {ghost: fallback_name(ghost)}
    assert cache.data == {}, "a fallback name must never be cached as if it were the real one"


@pytest.mark.parametrize(
    "auth",
    [
        FakeAuth(status=500),
        FakeAuth(status=401),
        FakeAuth(error=httpx.ConnectError("auth is down")),
        FakeAuth(error=httpx.ReadTimeout("auth is slow")),
    ],
    ids=["500", "401", "connection-refused", "timeout"],
)
async def test_auth_failures_degrade_to_fallback_names_and_cache_nothing(auth):
    ana = uuid.uuid4()
    service, cache = build(auth)

    names = await service.get_names([ana], TOKEN)

    assert names == {ana: fallback_name(ana)}
    assert cache.data == {}


async def test_cached_names_survive_an_auth_outage():
    ana, luis = uuid.uuid4(), uuid.uuid4()
    service, cache = build(FakeAuth(status=500))
    cache.set(CACHE_KEY.format(ana), "Ana")

    names = await service.get_names([ana, luis], TOKEN)

    assert names == {ana: "Ana", luis: fallback_name(luis)}


async def test_redis_down_still_resolves_names_from_auth():
    ana = uuid.uuid4()
    service, _ = build(FakeAuth({ana: "Ana"}), FakeCache(fail=True))

    names = await service.get_names([ana], TOKEN)

    assert names == {ana: "Ana"}


async def test_no_ids_means_no_calls():
    auth = FakeAuth()
    service, _ = build(auth)

    assert await service.get_names([], TOKEN) == {}
    assert auth.requests == []


async def test_a_long_list_is_split_into_batches_auth_accepts():
    ids = [uuid.uuid4() for _ in range(MAX_IDS_PER_REQUEST * 2 + 1)]
    auth = FakeAuth({player_id: f"p{n}" for n, player_id in enumerate(ids)})
    service, _ = build(auth)

    names = await service.get_names(ids, TOKEN)

    assert len(names) == len(ids)
    batch_sizes = [len(batch) for batch in auth.asked_ids()]
    assert batch_sizes == [MAX_IDS_PER_REQUEST, MAX_IDS_PER_REQUEST, 1]
