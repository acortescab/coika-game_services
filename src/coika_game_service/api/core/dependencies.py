from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.core.exceptions import InvalidTokenError
from coika_game_service.api.core.security import oauth2_scheme
from coika_game_service.api.services.auth_service import AuthService


def get_auth_service(request: Request) -> AuthService:
    """
    Get the auth service from the request's app state.
    """
    return AuthService(request.app.state.jwks)

async def get_writer_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_writer() as session:
        yield session

async def get_reader_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_reader() as session:
        yield session

def get_redis(request: Request):
    return request.app.state.redis

def get_auth_client(request: Request):
    return request.app.state.http

async def bearer_token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(oauth2_scheme)],
        auth: Annotated[AuthService, Depends(get_auth_service)]) -> str:
    """
    The raw access token of the request, to forward it to the auth service.
    """
    if credentials is None:
        raise InvalidTokenError("missing token")

    player_id = await auth.get_player_id_from_token(credentials.credentials)
    if not player_id:
        raise InvalidTokenError("missing token")
    
    return credentials.credentials

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
BearerToken = Annotated[str, Depends(bearer_token)]