from uuid import UUID

from coika_game_service.api.core.security import JWKSCache, verify_token


class AuthService:
    """
    Auth and validation service
    """
    def __init__(self, jwks: JWKSCache):
        """
        Initializes the AuthService with a JWKSCache.
        """
        self.jwks = jwks

    async def get_player_id_from_token(self, token: str) -> UUID:
        """
        Get the player ID from a token.
        """
        return await verify_token(token, self.jwks)