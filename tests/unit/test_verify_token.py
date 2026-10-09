import time
from uuid import UUID

import httpx
import jwt
import pytest

from coika_game_service.api.core.jwks import JWKSCache, JWKSUnavailable
from coika_game_service.api.core.security import InvalidTokenError, verify_token
from tests.auth_helpers import KID, make_jwks, make_keypair, make_token

PLAYER_ID = UUID("0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162")


@pytest.fixture
def keypair():
    return make_keypair()

@pytest.fixture
def jwks(keypair):

    def handler(_request):
        return httpx.Response(200, json=make_jwks(keypair))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JWKSCache("http://auth/jwks", 300, client)

async def test_valid_token_returns_player_id(keypair, jwks):
    token = make_token(keypair)
    assert await verify_token(token, jwks) == PLAYER_ID

async def test_expired_token_is_rejected(keypair, jwks):
    token = make_token(keypair, exp=int(time.time()) - 3600)
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)

async def test_token_expired_a_few_seconds_ago_is_accepted_thanks_to_leeway(keypair, jwks):
    """Clocks of two services are never identical: a small tolerance is expected (leeway=10)."""
    token = make_token(keypair, exp=int(time.time()) - 5)
    assert await verify_token(token, jwks) == PLAYER_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "other-service"},
        {"iss": "someone-else"},
        {"type": "refresh"},
        {"sub": "not-a-uuid"},
        {"exp": None},
        {"iat": None},
        {"sub": None},
        {"iss": None},
        {"aud": None},
    ],
    ids=[
        "wrong-audience",
        "wrong-issuer",
        "refresh-token",
        "sub-not-a-uuid",
        "missing-exp",
        "missing-iat",
        "missing-sub",
        "missing-iss",
        "missing-aud",
    ],
)
async def test_token_with_bad_or_missing_claims_is_rejected(keypair, jwks, overrides):
    token = make_token(keypair, **overrides)
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)


async def test_token_signed_with_another_key_is_rejected(jwks):
    """Same kid as the published key, but signed by an attacker's key."""
    token = make_token(make_keypair())
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)


async def test_token_with_unknown_kid_is_rejected(keypair, jwks):
    token = make_token(keypair, kid="nope")
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)


async def test_token_without_kid_is_rejected(keypair, jwks):
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(PLAYER_ID), "type": "access", "iss": "coika-auth", "aud": "coika-game",
         "iat": now, "exp": now + 600},
        keypair,
        algorithm="RS256",
    )
    with pytest.raises(InvalidTokenError, match="kid"):
        await verify_token(token, jwks)


@pytest.mark.parametrize("garbage", ["not-a-jwt", "", "a.b.c"])
async def test_malformed_token_is_rejected(jwks, garbage):
    with pytest.raises(InvalidTokenError):
        await verify_token(garbage, jwks)


async def test_alg_none_token_is_rejected(jwks):
    """The algorithm allow-list is fixed: an unsigned token must never be accepted."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(PLAYER_ID), "type": "access", "iss": "coika-auth", "aud": "coika-game",
         "iat": now, "exp": now + 600},
        key=None,
        algorithm="none",
        headers={"kid": KID},
    )
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)


async def test_hs256_token_is_rejected(jwks):
    """A token signed with a shared secret must not pass: only RS256 is allowed."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(PLAYER_ID), "type": "access", "iss": "coika-auth", "aud": "coika-game",
         "iat": now, "exp": now + 600},
        "some-shared-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
        headers={"kid": KID},
    )
    with pytest.raises(InvalidTokenError):
        await verify_token(token, jwks)


async def test_jwks_down_without_cache_is_not_a_token_error(keypair):
    """A broken auth service is the server's problem (503), never a 401 for the player."""

    def handler(_request):
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    jwks = JWKSCache("http://auth/jwks", 300, client)

    with pytest.raises(JWKSUnavailable):
        await verify_token(make_token(keypair), jwks)
