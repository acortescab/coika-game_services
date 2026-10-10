import uuid
from types import SimpleNamespace

import httpx
import pytest
from redis.exceptions import RedisError

from coika_game_service.api.core.config import settings
from coika_game_service.api.core.dependencies import current_player
from coika_game_service.api.core.exceptions import GameModeNotFound, RateLimitBlock
from coika_game_service.api.core.factories import get_match_service, get_score_service
from coika_game_service.api.core.game_modes import CLASSIC_GAME_MODE_ID
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest
from coika_game_service.api.services.match_service import MatchService
from coika_game_service.api.services.score_service import ScoreService
from coika_game_service.main import create_app

PLAYER = uuid.UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")
OTHER_PLAYER = uuid.UUID("7a1c2d3e-4f50-4a61-8b72-9c0d1e2f3a4b")
MATCH_ID = uuid.UUID("5f1d7a40-8c3e-4b6a-9d21-7e0c4a9b3f58")
KEY = uuid.UUID("c0ffee00-0000-4000-8000-000000000001")
PAYLOAD = CreateScoreRequest(score=1500, pieces_dropped=120, highest_tier=7)

LIMIT = 3
WINDOW = 60


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    """A limit of 3 per minute, so the tests do not need to send 50 requests."""
    monkeypatch.setattr(settings, "RATE_LIMIT_COUNTER", LIMIT)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", WINDOW)


class FakeRedis:
    """
    A Redis with counters and a clock. The pipeline runs its commands one after another when it
    executes, like MULTI/EXEC. `expire` has the `nx` option of redis-py (only set it if the key
    has no expiry), and `advance` moves the clock so the keys expire.
    """

    def __init__(self, fail=False):
        self.fail = fail
        self.now = 0
        self.values = {}
        self.expires = {}
        self.transactions = []

    def advance(self, seconds):
        self.now += seconds
        for key in [k for k, at in self.expires.items() if at <= self.now]:
            del self.values[key]
            del self.expires[key]

    def pipeline(self, transaction=True):
        if self.fail:
            raise RedisError("redis down")
        return FakePipeline(self, transaction)


class FakePipeline:
    def __init__(self, redis, transaction):
        self.redis = redis
        self.transaction = transaction
        self.queued = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.queued.append(("incr", key))

    def expire(self, key, seconds, nx=False):
        self.queued.append(("expire", key, seconds, nx))

    def ttl(self, key):
        self.queued.append(("ttl", key))

    async def execute(self):
        redis = self.redis
        redis.transactions.append((self.transaction, [command[0] for command in self.queued]))
        results = []
        for name, key, *args in self.queued:
            if name == "incr":
                redis.values[key] = redis.values.get(key, 0) + 1
                results.append(redis.values[key])
            elif name == "expire":
                seconds, nx = args
                if key in redis.values and not (nx and key in redis.expires):
                    redis.expires[key] = redis.now + seconds
                    results.append(True)
                else:
                    results.append(False)
            else:
                results.append(redis.expires[key] - redis.now if key in redis.expires else -1)
        return results


async def hits(repo, times, player=PLAYER, prefix="create_match"):
    return [await repo.hit_rate_limit(player, prefix) for _ in range(times)]


# --- Redis repository: the counter -----------------------------------------------------------


async def test_requests_up_to_the_limit_are_allowed_and_the_next_one_is_blocked():
    results = await hits(RedisRepository(FakeRedis()), LIMIT + 1)

    assert [blocked for blocked, _, _ in results] == [False, False, False, True]


async def test_remaining_counts_down_to_zero_and_never_goes_negative():
    results = await hits(RedisRepository(FakeRedis()), LIMIT + 2)

    assert [remaining for _, remaining, _ in results] == [2, 1, 0, 0, 0]


async def test_the_ttl_is_the_time_left_until_the_window_resets():
    redis = FakeRedis()
    repo = RedisRepository(redis)

    _, _, first = await repo.hit_rate_limit(PLAYER, "create_match")
    redis.advance(20)
    _, _, later = await repo.hit_rate_limit(PLAYER, "create_match")

    assert first == WINDOW
    assert later == WINDOW - 20


async def test_a_new_request_does_not_extend_the_window():
    """EXPIRE NX: only the first request of the window sets the expiry."""
    redis = FakeRedis()
    repo = RedisRepository(redis)

    await hits(repo, 2)
    redis.advance(WINDOW - 1)
    _, _, ttl = await repo.hit_rate_limit(PLAYER, "create_match")

    assert ttl == 1


async def test_the_counter_starts_again_when_the_window_expires():
    redis = FakeRedis()
    repo = RedisRepository(redis)
    await hits(repo, LIMIT + 1)

    redis.advance(WINDOW)
    blocked, remaining, ttl = await repo.hit_rate_limit(PLAYER, "create_match")

    assert (blocked, remaining, ttl) == (False, LIMIT - 1, WINDOW)


async def test_each_player_has_their_own_counter():
    repo = RedisRepository(FakeRedis())
    await hits(repo, LIMIT + 1)

    blocked, remaining, _ = await repo.hit_rate_limit(OTHER_PLAYER, "create_match")

    assert (blocked, remaining) == (False, LIMIT - 1)


async def test_each_operation_has_its_own_counter():
    repo = RedisRepository(FakeRedis())
    await hits(repo, LIMIT + 1, prefix="create_match")

    blocked, remaining, _ = await repo.hit_rate_limit(PLAYER, "create_score")

    assert (blocked, remaining) == (False, LIMIT - 1)


async def test_the_key_names_the_player_and_the_operation():
    redis = FakeRedis()

    await RedisRepository(redis).hit_rate_limit(PLAYER, "create_score")

    assert list(redis.values) == [f"ratelimit:{PLAYER}:create_score"]


async def test_the_count_and_its_expiry_are_set_in_one_transaction():
    """If they were separate commands, a crash between them would leave a key that never expires."""
    redis = FakeRedis()

    await RedisRepository(redis).hit_rate_limit(PLAYER, "create_match")

    assert redis.transactions == [(True, ["incr", "expire", "ttl"])]
    assert list(redis.expires) == list(redis.values)


async def test_redis_down_lets_the_request_through():
    blocked, remaining, ttl = await RedisRepository(FakeRedis(fail=True)).hit_rate_limit(
        PLAYER, "create_match"
    )

    assert blocked is False
    assert remaining == LIMIT
    assert ttl == 0


# --- Services: where the limit is applied ----------------------------------------------------


class FakeCache:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def hit_rate_limit(self, player_id, prefix):
        self.calls.append((player_id, prefix))
        return self.result


class MustNotBeUsed:
    """A repository that fails the test if the service touches it."""

    def __getattr__(self, name):
        raise AssertionError(f"{name} was called after the rate limit blocked the request")


class NoGameMode:
    async def get_game_mode(self, game_mode_id):
        return None


async def test_a_blocked_match_creation_raises_with_the_remaining_and_the_reset():
    cache = FakeCache((True, 0, 42))
    service = MatchService(MustNotBeUsed(), MustNotBeUsed(), cache, MustNotBeUsed())

    with pytest.raises(RateLimitBlock) as error:
        await service.create_match(PLAYER, CLASSIC_GAME_MODE_ID, KEY)

    assert (error.value.remaining, error.value.ttl) == (0, 42)
    assert cache.calls == [(PLAYER, "create_match")]


async def test_a_match_creation_under_the_limit_goes_on_to_the_normal_flow():
    cache = FakeCache((False, 2, 50))
    service = MatchService(MustNotBeUsed(), NoGameMode(), cache, MustNotBeUsed())

    with pytest.raises(GameModeNotFound):
        await service.create_match(PLAYER, CLASSIC_GAME_MODE_ID, KEY)

    assert cache.calls == [(PLAYER, "create_match")]


async def test_a_blocked_score_submission_raises_before_touching_the_database():
    cache = FakeCache((True, 0, 17))
    service = ScoreService(
        MustNotBeUsed(), MustNotBeUsed(), cache, MustNotBeUsed(), MustNotBeUsed()
    )

    with pytest.raises(RateLimitBlock) as error:
        await service.create_score(PLAYER, MATCH_ID, PAYLOAD)

    assert (error.value.remaining, error.value.ttl) == (0, 17)
    assert cache.calls == [(PLAYER, "create_score")]


# --- HTTP: the 429 response ------------------------------------------------------------------


class BlockedService:
    """Stands in for a service whose player is over the limit."""

    def __init__(self, remaining=0, ttl=42):
        self.error = RateLimitBlock("player", remaining, ttl)

    async def create_match(self, *args):
        raise self.error

    async def create_score(self, *args):
        raise self.error


def app_for(factory, service):
    app = create_app()
    app.dependency_overrides[factory] = lambda: service
    app.dependency_overrides[current_player] = lambda: PLAYER
    return app


async def post_match(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/matches",
            headers={"Idempotency-Key": str(KEY)},
            json={"game_mode_id": str(CLASSIC_GAME_MODE_ID)},
        )


async def post_score(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/matches/{MATCH_ID}/score",
            json={"score": 1500, "pieces_dropped": 120, "highest_tier": 7},
        )


@pytest.mark.parametrize(
    ("factory", "send"),
    [(get_match_service, post_match), (get_score_service, post_score)],
    ids=["create-match", "create-score"],
)
async def test_over_the_limit_the_answer_is_429_with_retry_after_and_the_rate_limit_headers(
    factory, send
):
    response = await send(app_for(factory, BlockedService(remaining=0, ttl=42)))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.headers["X-RateLimit-Limit"] == str(LIMIT)
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["X-RateLimit-Reset"] == "42"


async def test_retry_after_is_the_time_left_in_the_window_not_a_fixed_value():
    short = await post_match(app_for(get_match_service, BlockedService(ttl=3)))
    long = await post_match(app_for(get_match_service, BlockedService(ttl=110)))

    assert short.headers["Retry-After"] == "3"
    assert long.headers["Retry-After"] == "110"


# --- End to end: real repository and service, only the stores are fake -----------------------


class StubMatchRepo:
    async def lock_player(self, player_id):
        pass

    async def get_by_idempotency_key(self, player_id, key, use_writer=False):
        return None

    async def abandon_open_matches(self, player_id):
        pass

    async def create_match(self, player_id, game_mode_id, key, started_at, seed):
        return SimpleNamespace(id=MATCH_ID, status="in_progress", seed=seed)


class FoundGameMode:
    async def get_game_mode(self, game_mode_id):
        return SimpleNamespace(id=game_mode_id)


class StubSession:
    async def commit(self):
        pass


def real_flow_app(redis, player=PLAYER):
    service = MatchService(
        StubMatchRepo(), FoundGameMode(), RedisRepository(redis), StubSession()
    )
    app = app_for(get_match_service, service)
    app.dependency_overrides[current_player] = lambda: player
    return app


async def test_the_request_after_the_limit_is_a_429_and_the_ones_before_are_201():
    redis = FakeRedis()
    app = real_flow_app(redis)

    statuses = [(await post_match(app)).status_code for _ in range(LIMIT + 1)]

    assert statuses == [201] * LIMIT + [429]


async def test_the_429_of_a_real_counter_reports_the_seconds_to_the_reset():
    redis = FakeRedis()
    app = real_flow_app(redis)
    for _ in range(LIMIT):
        await post_match(app)
    redis.advance(25)

    response = await post_match(app)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(WINDOW - 25)
    assert response.headers["X-RateLimit-Reset"] == str(WINDOW - 25)
    assert response.headers["X-RateLimit-Remaining"] == "0"


async def test_after_the_window_the_player_can_send_again():
    redis = FakeRedis()
    app = real_flow_app(redis)
    for _ in range(LIMIT + 1):
        await post_match(app)

    redis.advance(WINDOW)
    response = await post_match(app)

    assert response.status_code == 201


async def test_one_player_over_the_limit_does_not_block_another():
    redis = FakeRedis()
    for _ in range(LIMIT + 1):
        await post_match(real_flow_app(redis, PLAYER))

    response = await post_match(real_flow_app(redis, OTHER_PLAYER))

    assert response.status_code == 201


async def test_with_redis_down_requests_are_not_limited():
    app = real_flow_app(FakeRedis(fail=True))

    statuses = [(await post_match(app)).status_code for _ in range(LIMIT + 2)]

    assert statuses == [201] * (LIMIT + 2)
