from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.clients.auth_client import AuthClient
from coika_game_service.api.core.config import settings
from coika_game_service.api.core.security import InvalidTokenError, oauth2_scheme
from coika_game_service.api.db.dependendencies import get_reader_session, get_writer_session
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.services.auth_service import AuthService
from coika_game_service.api.services.match_service import MatchService
from coika_game_service.api.services.player_name_service import PlayerNameService
from coika_game_service.api.services.score_service import ScoreService


def get_auth_service(request: Request) -> AuthService:
    """
    Get the auth service from the request's app state.
    """
    return AuthService(request.app.state.jwks)

def get_match_service(
        write_db: Annotated[AsyncSession, Depends(get_writer_session)], 
        read_db: Annotated[AsyncSession, Depends(get_reader_session)]):
    """
    Create a match service with the given database sessions.
    """
    repo = MatchRepository(read_db, write_db)
    return MatchService(repo, write_db)

def get_score_service(
        write_db: Annotated[AsyncSession, Depends(get_writer_session)], 
        read_db: Annotated[AsyncSession, Depends(get_reader_session)]):
    """
    Create a score service with the given database sessions.
    """
    score_repo = ScoreRepository(read_db, write_db)
    match_repo = MatchRepository(read_db, write_db)
    return ScoreService(score_repo, match_repo, write_db)


def get_player_name_service(request: Request) -> PlayerNameService:
    """
    Get the service that resolves player names through the auth service, cached in Redis.
    """
    client = AuthClient(request.app.state.http, settings.AUTH_PLAYERS_URL)
    return PlayerNameService(
        client, request.app.state.redis, settings.PLAYER_NAME_CACHE_TTL_SECONDS
    )

async def current_player(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(oauth2_scheme)],
        auth: Annotated[AuthService, Depends(get_auth_service)]) -> UUID:
    """
    Get the id of the authenticated player (the token's `sub`) from the request.
    """
    if credentials is None:
        raise InvalidTokenError("missing token")
    return await auth.get_player_id_from_token(credentials.credentials)

CurrentPlayer = Annotated[UUID, Depends(current_player)]