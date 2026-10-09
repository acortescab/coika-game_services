import uuid

import jwt
from fastapi.security import HTTPBearer

from coika_game_service.api.core.config import settings
from coika_game_service.api.core.jwks import JWKSCache, UnknownKeyError

oauth2_scheme = HTTPBearer(auto_error=False)

class InvalidTokenError(Exception):
    """The access token is not acceptable. Always maps to a 401."""

async def verify_token(token: str, jwks: JWKSCache) -> uuid.UUID:
    """
    Verify that an access token is valid and return the subject.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        # This is a malformed JWT, so we can just bail out here.
        raise InvalidTokenError("malformed token") from exc

    kid = header.get("kid")
    if not isinstance(kid, str):
        # No key id, so we can't find the signing key.
        raise InvalidTokenError("missing kid")

    try:
        key = await jwks.get_key(kid)
    except UnknownKeyError as exc:
        # The key cannot be fetched from jwks cache
        raise InvalidTokenError("unknown kid") from exc

    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=settings.AUTH_AUDIENCE,
            issuer=settings.AUTH_ISSUER,
            leeway=10,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        # This will catch any PyJWTError, including ExpiredSignatureError,
        # InvalidAudienceError, etc.
        raise InvalidTokenError(type(exc).__name__) from exc

    if payload.get("type") != "access":
        # The token is not an access token.
        raise InvalidTokenError("not an access token")

    try:
        return uuid.UUID(payload["sub"])
    except (ValueError, TypeError, AttributeError) as exc:
        # The subject is not a valid UUID.
        raise InvalidTokenError("invalid sub") from exc